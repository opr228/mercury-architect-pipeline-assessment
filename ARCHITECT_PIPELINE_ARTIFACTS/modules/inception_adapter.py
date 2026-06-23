"""OpenAI-compatible client for Inception Labs Mercury API (lab / OPRAI provider)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from modules.codebase_context import build_system_prefix, context_enabled
from modules.inception_agent_policy import (
    agent_max_tokens,
    build_agent_system_messages,
    build_evidence_state_block,
    clamp_mercury_temperature,
    deliverable_write_pending,
    extract_required_deliverable_path,
    extract_task_phase,
    extract_plan_path_from_message,
    mandatory_evidence_pending,
    max_agent_nudges,
    max_agent_tool_turns,
    phase_agent_budgets,
    resolve_consult_only,
    resolve_reasoning_effort,
    resolve_temperature,
    should_use_agent_loop,
    task_is_audit,
    task_is_roadmap,
    task_requires_deliverable_write,
    task_requires_forced_first_tool,
    task_requires_tool_evidence,
    trim_agent_thread,
)
from modules.inception_skill_loader import load_matching_skill
from modules.inception_tool_helpers import ToolCallLoopDetector, recover_tool_args
from modules.inception_agent_tools import (
    AgentRuntime,
    execute_tool,
    tool_schemas,
)
from modules.inception_thread_compat import normalize_messages_for_mercury
from modules.llm_trace import log_llm_trace
from modules.remote_context import load_remote_context_from_env
from modules.stream_text import stream_text_delta

logger = logging.getLogger(__name__)


@dataclass
class InceptionResult:
    success: bool
    content: str
    model: str
    error: Optional[str] = None
    latency_ms: Optional[int] = None
    tool_steps: int = 0
    inception_id: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)


def inception_api_key() -> str:
    return os.getenv("INCEPTION_API_KEY", "").strip()


def inception_chat_url() -> str:
    base = os.getenv("INCEPTION_API_BASE", "https://api.inceptionlabs.ai/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def inception_fim_url() -> str:
    base = os.getenv("INCEPTION_API_BASE", "https://api.inceptionlabs.ai/v1").rstrip("/")
    if base.endswith("/fim/completions"):
        return base
    return f"{base}/fim/completions"


def _normalize_model(model: Optional[str]) -> str:
    raw = (model or os.getenv("LLM_MODEL", "mercury-2")).strip()
    if raw.lower() in ("auto", "mercury 2", "mercury2"):
        return "mercury-2"
    return raw


def _chat_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {inception_api_key()}",
    }


def _extract_usage(data: Dict[str, Any]) -> Dict[str, int]:
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    out: Dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_input_tokens"):
        raw = usage.get(key)
        if raw is not None:
            try:
                out[key] = int(raw)
            except (TypeError, ValueError):
                continue
    return out


def _log_inception_usage(
    *,
    model: str,
    data: Dict[str, Any],
    latency_ms: int,
    success: bool,
    error: Optional[str] = None,
    synthesis_step: bool = False,
    tool_steps: int = 0,
    stream: bool = False,
) -> Dict[str, int]:
    usage = _extract_usage(data)
    request_id = os.getenv("OPRAI_REQUEST_ID", "").strip()
    log_llm_trace(
        {
            "request_id": request_id,
            "provider": "inception",
            "model": model,
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
            "endpoint": "/v1/chat/completions",
            "stream": stream,
            "synthesis_step": synthesis_step,
            "tool_steps": tool_steps,
            "inception_id": data.get("id"),
            **usage,
        }
    )
    return usage


def _iter_sse_deltas(response: requests.Response) -> Iterator[Tuple[str, Any]]:
    """Parse OpenAI-style SSE from Mercury stream=true."""
    accumulated = ""
    last_payload: Dict[str, Any] = {}
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            last_payload = payload
        for choice in payload.get("choices", []) if isinstance(payload, dict) else []:
            delta_obj = choice.get("delta") or {}
            incoming = delta_obj.get("content")
            if incoming is None:
                continue
            incoming_text = str(incoming)
            if not incoming_text:
                continue
            delta = stream_text_delta(accumulated, incoming_text)
            if not delta and incoming_text and incoming_text.startswith(accumulated):
                delta = incoming_text[len(accumulated) :]
            if not delta:
                delta = incoming_text
            accumulated += delta
            yield ("delta", delta)
    yield ("complete", {"content": accumulated.strip(), "data": last_payload})


def _post_chat_stream(payload: Dict[str, Any], timeout_seconds: int) -> Iterator[Tuple[str, Any]]:
    stream_payload = {**payload, "stream": True}
    try:
        response = requests.post(
            inception_chat_url(),
            headers=_chat_headers(),
            json=stream_payload,
            timeout=timeout_seconds,
            stream=True,
        )
    except Exception as exc:
        yield ("error", str(exc))
        return
    if response.status_code != 200:
        yield ("error", response.text[:500])
        return
    yield from _iter_sse_deltas(response)


def _build_agent_thread(
    messages: List[Dict[str, Any]],
    *,
    explore_mode: bool,
    allow_writes: bool,
    user_text: str = "",
    skill_block: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], AgentRuntime]:
    workspace = os.getenv(
        "CURSOR_AGENT_WORKSPACE",
        os.getenv("OPRAI_INSTANCE_ROOT", "/home/opr"),
    ).strip()
    consult_only = resolve_consult_only(user_text)
    phase = extract_task_phase(user_text)
    plan_path = extract_plan_path_from_message(user_text)
    lab_target = os.getenv("OPRAI_TARGET_KIND", "").strip().lower() == "lab"
    runtime = AgentRuntime(
        explore_mode=explore_mode,
        allow_writes=allow_writes and not consult_only,
        consult_only=consult_only,
        lab_target=lab_target,
        workspace=workspace,
        task_phase=phase,
        plan_path=plan_path,
    )
    thread: List[Dict[str, Any]] = []
    policy_blocks = build_agent_system_messages(runtime)
    system_primary: List[str] = []
    if context_enabled():
        system_primary.append(build_system_prefix(explore_mode=explore_mode))
    system_primary.append(policy_blocks[0])
    if skill_block:
        system_primary.append(f"<skill>\n{skill_block}\n</skill>")
    remote_block = load_remote_context_from_env()
    if remote_block:
        system_primary.append(remote_block)
    thread.append({"role": "system", "content": "\n\n".join(system_primary)})
    thread.append({"role": "system", "content": policy_blocks[1]})
    thread.extend(messages)
    return thread, runtime


def _post_chat(payload: Dict[str, Any], timeout_seconds: int) -> tuple[int, Dict[str, Any], str]:
    send_payload = dict(payload)
    messages = send_payload.get("messages")
    if isinstance(messages, list):
        send_payload["messages"] = normalize_messages_for_mercury(messages)
    response = requests.post(
        inception_chat_url(),
        headers=_chat_headers(),
        json=send_payload,
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        err_text = response.text[:500]
        if response.status_code == 400 and "role" in err_text.lower():
            retry_payload = dict(send_payload)
            retry_payload["messages"] = normalize_messages_for_mercury(
                retry_payload.get("messages") or []
            )
            retry = requests.post(
                inception_chat_url(),
                headers=_chat_headers(),
                json=retry_payload,
                timeout=timeout_seconds,
            )
            if retry.status_code == 200:
                try:
                    return retry.status_code, retry.json(), ""
                except ValueError:
                    return retry.status_code, {}, "invalid JSON response"
        return response.status_code, {}, err_text
    try:
        return response.status_code, response.json(), ""
    except ValueError:
        return response.status_code, {}, "invalid JSON response"


def _force_audit_tool_enabled() -> bool:
    return os.getenv("INCEPTION_FORCE_AUDIT_TOOL", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _is_tool_choice_unsupported(err_text: str) -> bool:
    lowered = (err_text or "").lower()
    return "tool_choice" in lowered or "unsupported" in lowered or "required" in lowered


def _api_extras(
    reasoning_effort: str,
    *,
    synthesis_step: bool,
    streaming: bool = False,
) -> Dict[str, Any]:
    extras: Dict[str, Any] = {"reasoning_effort": reasoning_effort}
    if os.getenv("INCEPTION_REASONING_SUMMARY", "0").strip().lower() in ("0", "false", "no"):
        extras["reasoning_summary"] = False
    if not synthesis_step and os.getenv("INCEPTION_REALTIME", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        extras["realtime"] = True
    if streaming and os.getenv("INCEPTION_DIFFUSING", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        extras["diffusing"] = True
    return extras


def call_chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.5,
    reasoning_effort: Optional[str] = None,
    timeout_seconds: int = 120,
    tools: Optional[List[Dict[str, Any]]] = None,
    explore_mode: bool = False,
) -> InceptionResult:
    """Mercury 2 — v1/chat/completions (optional tools)."""
    api_key = inception_api_key()
    if not api_key:
        return InceptionResult(
            success=False,
            content="",
            model=model or "",
            error="INCEPTION_API_KEY is not configured",
        )

    target_model = _normalize_model(model)
    thread: List[Dict[str, Any]] = []
    if context_enabled() and os.getenv("INCEPTION_CHAT_INCLUDE_CONTEXT", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        thread.append({"role": "system", "content": build_system_prefix(explore_mode=explore_mode)})
    thread.extend(messages)
    payload: Dict[str, Any] = {
        "model": target_model,
        "messages": thread,
        "max_tokens": max_tokens,
        "temperature": clamp_mercury_temperature(temperature),
    }
    effort = reasoning_effort or os.getenv("INCEPTION_REASONING_EFFORT", "medium").strip()
    payload.update(_api_extras(effort, synthesis_step=not bool(tools)))
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    started = time.monotonic()
    try:
        status, data, err_text = _post_chat(payload, timeout_seconds)
        latency_ms = int((time.monotonic() - started) * 1000)
        if status != 200:
            _log_inception_usage(
                model=target_model,
                data={},
                latency_ms=latency_ms,
                success=False,
                error=err_text[:200],
            )
            return InceptionResult(
                success=False,
                content="",
                model=target_model,
                error=f"Inception chat failed ({status}): {err_text}",
                latency_ms=latency_ms,
            )
        message = data.get("choices", [{}])[0].get("message", {}) or {}
        content = message.get("content") or ""
        if not content and message.get("tool_calls"):
            content = ""
        usage = _log_inception_usage(
            model=target_model,
            data=data,
            latency_ms=latency_ms,
            success=True,
        )
        return InceptionResult(
            success=True,
            content=content,
            model=target_model,
            latency_ms=latency_ms,
            inception_id=data.get("id"),
            usage=usage,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return InceptionResult(
            success=False,
            content="",
            model=target_model,
            error=str(exc),
            latency_ms=latency_ms,
        )


def _user_text_from_messages(messages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for item in messages:
        if item.get("role") == "user":
            parts.append(str(item.get("content", "")))
    return "\n".join(parts).strip()


def _build_agent_nudge(
    *,
    user_text: str,
    runtime: AgentRuntime,
    steps: int,
    deliverable_path: Optional[str],
) -> Optional[str]:
    if runtime.consult_only:
        return None
    if steps > 0:
        return None
    if not task_requires_tool_evidence(user_text, consult_only=runtime.consult_only):
        return None
    if runtime.allow_writes and task_requires_deliverable_write(
        user_text, consult_only=runtime.consult_only
    ):
        target = deliverable_path or "the required deliverable path"
        return (
            f"You stopped without calling tools. Call read_file/grep_search for evidence, "
            f"then write_file to create {target}. Do not describe the file — write it."
        )
    return (
        "You must call grep_search or read_file before answering. "
        "Do not invent repo facts, metrics, or file contents."
    )


def _deliverable_gate_failed(path: str, runtime: AgentRuntime) -> Optional[str]:
    from modules.deliverable_validator import validate_deliverable
    from modules.instance_paths import resolve_deliverable_path

    resolved = str(resolve_deliverable_path(path, runtime.workspace))
    if resolved in runtime.write_paths or any(
        str(resolve_deliverable_path(p, runtime.workspace)) == resolved for p in runtime.write_paths
    ):
        result = validate_deliverable(resolved, task_class="PLAN")
        if not result.stub:
            return None
        return result.reason or "deliverable stub"
    result = validate_deliverable(resolved, task_class="PLAN")
    if result.stub:
        return result.reason or "deliverable missing"
    return None


def call_agent(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.5,
    explore_mode: bool = False,
    allow_writes: bool = False,
    timeout_seconds: Optional[int] = None,
) -> InceptionResult:
    """Mercury 2 agent loop with OPRAI tools (direct API, no Continue CLI)."""
    api_key = inception_api_key()
    if not api_key:
        return InceptionResult(
            success=False,
            content="",
            model=model or "",
            error="INCEPTION_API_KEY is not configured",
        )

    target_model = _normalize_model(model)
    user_text = _user_text_from_messages(messages)
    task_phase = extract_task_phase(user_text)
    phase_steps, phase_gates = phase_agent_budgets(task_phase)
    max_steps = int(os.getenv("INCEPTION_AGENT_MAX_STEPS", str(phase_steps)))
    if task_phase:
        max_steps = phase_steps
    per_call_timeout = timeout_seconds or int(os.getenv("INCEPTION_AGENT_TIMEOUT_SECONDS", "120"))
    agent_temperature = resolve_temperature(agent_loop=True)
    tool_step_budget = max_tokens or agent_max_tokens(synthesis_step=False)

    request_id = os.getenv("OPRAI_REQUEST_ID", "").strip()
    try:
        from modules.agent_activity import activity_enabled, end_run, start_run, _log

        if activity_enabled() and request_id:
            preview = ""
            if messages:
                preview = str(messages[-1].get("content", ""))[:120]
            start_run(request_id, preview, os.getpid())
    except Exception:
        start_run = end_run = _log = None  # type: ignore
        activity_enabled = lambda: False  # type: ignore

    user_text = _user_text_from_messages(messages)
    workspace = os.getenv(
        "CURSOR_AGENT_WORKSPACE",
        os.getenv("OPRAI_INSTANCE_ROOT", "/home/opr"),
    ).strip()
    skill_block = load_matching_skill(workspace, user_text)
    thread, runtime = _build_agent_thread(
        messages,
        explore_mode=explore_mode,
        allow_writes=allow_writes,
        user_text=user_text,
        skill_block=skill_block,
    )

    tools = tool_schemas()
    max_tool_turns = max_agent_tool_turns()
    audit_task = task_is_audit(user_text) and not runtime.consult_only
    roadmap_task = task_is_roadmap(user_text) and not runtime.consult_only
    forced_first = task_requires_forced_first_tool(
        user_text, consult_only=runtime.consult_only
    )
    deliverable_path = extract_required_deliverable_path(user_text)
    if deliverable_path:
        from modules.instance_paths import normalize_workspace_relative_path

        deliverable_path = normalize_workspace_relative_path(
            deliverable_path, runtime.workspace
        )
    max_nudges = max_agent_nudges()
    nudge_count = 0
    gate_turns = 0
    max_gate_turns = phase_gates if task_phase else int(os.getenv("INCEPTION_MAX_GATE_TURNS", "6"))
    loop_detector = ToolCallLoopDetector()
    started = time.monotonic()
    steps = 0
    total_usage: Dict[str, int] = {}
    last_inception_id: Optional[str] = None

    def _merge_usage(usage: Dict[str, int]) -> None:
        nonlocal last_inception_id
        for key, value in usage.items():
            total_usage[key] = total_usage.get(key, 0) + int(value)

    try:
        for _ in range(max_steps):
            request_thread = trim_agent_thread(thread, max_tool_turns=max_tool_turns)
            synthesis_step = bool(request_thread) and request_thread[-1].get("role") == "tool"
            pre_synth_block = None
            if synthesis_step:
                # Hard gate (per plan): block synthesis until mandatory audit
                # reads are done and the required deliverable is written. This
                # uses a dedicated budget (max_gate_turns) — not the soft-nudge
                # budget — so a low MAX_NUDGES cannot let an audit finish without
                # evidence. tool_choice stays "auto" here (forcing "required"
                # makes Mercury call the wrong tool); the visible nudge guides it.
                pre_synth_block = mandatory_evidence_pending(runtime, user_text)
                if not pre_synth_block:
                    pre_synth_block = deliverable_write_pending(
                        deliverable_path, runtime, user_text
                    )
                if pre_synth_block and gate_turns < max_gate_turns:
                    gate_turns += 1
                    synthesis_step = False
                    thread.append({"role": "user", "content": pre_synth_block})
                elif pre_synth_block and deliverable_path:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    if activity_enabled() and request_id:
                        end_run(
                            request_id,
                            success=False,
                            latency_ms=latency_ms,
                            error=pre_synth_block[:200],
                        )
                    return InceptionResult(
                        success=False,
                        content="",
                        model=target_model,
                        error=f"evidence/deliverable gate exhausted: {pre_synth_block}",
                        latency_ms=latency_ms,
                        tool_steps=steps,
                        usage=total_usage,
                    )
            effort = resolve_reasoning_effort(
                agent_loop=True,
                explore_mode=explore_mode,
                allow_writes=allow_writes,
                tool_steps=steps,
                synthesis_step=synthesis_step,
                audit_task=audit_task,
                roadmap_task=roadmap_task,
                task_phase=runtime.task_phase or task_phase,
            )
            phase_or_audit = (audit_task or roadmap_task or runtime.task_phase) and not synthesis_step
            if phase_or_audit:
                evidence_block = build_evidence_state_block(runtime, user_text)
                if evidence_block:
                    request_thread = list(request_thread) + [
                        {"role": "system", "content": evidence_block}
                    ]
            step_max_tokens = agent_max_tokens(
                synthesis_step=synthesis_step, roadmap_task=roadmap_task
            )
            if not synthesis_step and max_tokens is not None:
                step_max_tokens = min(step_max_tokens, tool_step_budget)
            payload: Dict[str, Any] = {
                "model": target_model,
                "messages": request_thread,
                "max_tokens": step_max_tokens,
                "temperature": agent_temperature,
            }
            forced_tool_used = False
            if synthesis_step:
                payload["tool_choice"] = "none"
            else:
                payload["tools"] = tools
                if forced_first and steps == 0 and _force_audit_tool_enabled():
                    payload["tool_choice"] = "required"
                    forced_tool_used = True
                else:
                    payload["tool_choice"] = "auto"
            payload.update(_api_extras(effort, synthesis_step=synthesis_step))
            step_started = time.monotonic()
            status, data, err_text = _post_chat(payload, per_call_timeout)
            if (
                status != 200
                and forced_tool_used
                and _is_tool_choice_unsupported(err_text)
            ):
                payload["tool_choice"] = "auto"
                status, data, err_text = _post_chat(payload, per_call_timeout)
            step_latency_ms = int((time.monotonic() - step_started) * 1000)
            if status != 200:
                _log_inception_usage(
                    model=target_model,
                    data={},
                    latency_ms=step_latency_ms,
                    success=False,
                    error=err_text[:200],
                    synthesis_step=synthesis_step,
                    tool_steps=steps,
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                if activity_enabled() and request_id:
                    end_run(request_id, success=False, latency_ms=latency_ms, error=err_text[:200])
                return InceptionResult(
                    success=False,
                    content="",
                    model=target_model,
                    error=f"Inception agent failed ({status}): {err_text}",
                    latency_ms=latency_ms,
                    tool_steps=steps,
                    usage=total_usage,
                )

            step_usage = _log_inception_usage(
                model=target_model,
                data=data,
                latency_ms=step_latency_ms,
                success=True,
                synthesis_step=synthesis_step,
                tool_steps=steps,
            )
            _merge_usage(step_usage)
            if data.get("id"):
                last_inception_id = str(data["id"])

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {}) or {}
            tool_calls = message.get("tool_calls") or []
            content_raw = message.get("content")
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "" if content_raw is None else content_raw,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            thread.append(assistant_msg)

            if not tool_calls:
                content = (assistant_msg.get("content") or "").strip()
                gate_reason: Optional[str] = None
                if (
                    deliverable_path
                    and runtime.allow_writes
                    and not runtime.consult_only
                    and explore_mode
                    and task_requires_deliverable_write(
                        user_text, consult_only=runtime.consult_only
                    )
                ):
                    gate_reason = _deliverable_gate_failed(deliverable_path, runtime)
                if gate_reason:
                    if gate_turns < max_gate_turns:
                        gate_turns += 1
                        thread.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Deliverable gate failed ({gate_reason}). "
                                    f"Call write_file for {deliverable_path} before finishing."
                                ),
                            }
                        )
                        continue
                    latency_ms = int((time.monotonic() - started) * 1000)
                    if activity_enabled() and request_id:
                        end_run(
                            request_id,
                            success=False,
                            latency_ms=latency_ms,
                            error=gate_reason[:200],
                        )
                    return InceptionResult(
                        success=False,
                        content=content,
                        model=target_model,
                        error=f"deliverable gate failed ({gate_reason})",
                        latency_ms=latency_ms,
                        tool_steps=steps,
                        inception_id=last_inception_id,
                        usage=total_usage,
                    )
                nudge = _build_agent_nudge(
                    user_text=user_text,
                    runtime=runtime,
                    steps=steps,
                    deliverable_path=deliverable_path,
                )
                if nudge and nudge_count < max_nudges:
                    nudge_count += 1
                    thread.append({"role": "user", "content": nudge})
                    continue
                latency_ms = int((time.monotonic() - started) * 1000)
                if activity_enabled() and request_id:
                    end_run(request_id, success=bool(content), latency_ms=latency_ms)
                return InceptionResult(
                    success=bool(content),
                    content=content,
                    model=target_model,
                    error=None if content else "empty agent response",
                    latency_ms=latency_ms,
                    tool_steps=steps,
                    inception_id=last_inception_id,
                    usage=total_usage,
                )

            for tool_call in tool_calls:
                steps += 1
                fn = (tool_call.get("function") or {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = recover_tool_args(name, raw_args if isinstance(raw_args, str) else "")
                if not isinstance(args, dict):
                    args = {}
                if args.get("_parse_error"):
                    result = json.dumps({"error": args["_parse_error"]})
                else:
                    result = execute_tool(name, args, runtime)
                loop_msg = loop_detector.check(name, args, result)
                if activity_enabled() and request_id and _log:
                    _log(request_id, "tool", f"{name} {str(args)[:160]}")
                thread.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", f"call_{steps}"),
                        "content": result,
                    }
                )
                if loop_msg and nudge_count < max_nudges:
                    nudge_count += 1
                    thread.append({"role": "user", "content": loop_msg})
                    break

        latency_ms = int((time.monotonic() - started) * 1000)
        if activity_enabled() and request_id:
            end_run(request_id, success=False, latency_ms=latency_ms, error=f"max steps {max_steps}")
        return InceptionResult(
            success=False,
            content="",
            model=target_model,
            error=f"agent exceeded max steps ({max_steps})",
            latency_ms=latency_ms,
            tool_steps=steps,
            usage=total_usage,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        if activity_enabled() and request_id:
            end_run(request_id, success=False, latency_ms=latency_ms, error=str(exc)[:200])
        return InceptionResult(
            success=False,
            content="",
            model=target_model,
            error=str(exc),
            latency_ms=latency_ms,
            tool_steps=steps,
            usage=total_usage,
        )


def _build_chat_thread(
    messages: List[Dict[str, Any]],
    *,
    explore_mode: bool,
) -> List[Dict[str, Any]]:
    thread: List[Dict[str, Any]] = []
    if context_enabled() and os.getenv("INCEPTION_CHAT_INCLUDE_CONTEXT", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        thread.append({"role": "system", "content": build_system_prefix(explore_mode=explore_mode)})
    thread.extend(messages)
    return thread


def iter_call_stream(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    explore_mode: bool = False,
    allow_writes: bool = False,
    timeout_seconds: Optional[int] = None,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield SSE-ready (event_name, payload) tuples for Mercury 2 streaming."""
    api_key = inception_api_key()
    request_id = os.getenv("OPRAI_REQUEST_ID", "").strip() or uuid.uuid4().hex[:12]
    os.environ["OPRAI_REQUEST_ID"] = request_id
    if not api_key:
        yield ("error", {"error": "INCEPTION_API_KEY is not configured", "request_id": request_id})
        return

    target_model = _normalize_model(model)
    per_call_timeout = timeout_seconds or int(os.getenv("INCEPTION_AGENT_TIMEOUT_SECONDS", "120"))
    user_text = ""
    for item in reversed(messages):
        if item.get("role") == "user":
            user_text = str(item.get("content", ""))
            break

    yield ("meta", {"request_id": request_id, "status": "started", "provider": "inception"})

    if not should_use_agent_loop(user_text, explore_mode=explore_mode, allow_writes=allow_writes):
        chat_budget = max_tokens or min(agent_max_tokens(synthesis_step=True), 2048)
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": _build_chat_thread(messages, explore_mode=explore_mode),
            "max_tokens": chat_budget,
            "temperature": resolve_temperature(agent_loop=False),
        }
        payload.update(
            _api_extras(
                os.getenv("INCEPTION_CHAT_REASONING_EFFORT", "instant").strip() or "instant",
                synthesis_step=True,
                streaming=True,
            )
        )
        started = time.monotonic()
        accumulated = ""
        last_data: Dict[str, Any] = {}
        for event, value in _post_chat_stream(payload, per_call_timeout):
            if event == "error":
                yield ("error", {"error": str(value), "request_id": request_id})
                return
            if event == "delta":
                accumulated += str(value)
                yield ("chunk", {"text": str(value)})
            elif event == "complete":
                complete = value if isinstance(value, dict) else {}
                accumulated = str(complete.get("content") or accumulated)
                last_data = complete.get("data") if isinstance(complete.get("data"), dict) else {}
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = _log_inception_usage(
            model=target_model,
            data=last_data,
            latency_ms=latency_ms,
            success=bool(accumulated.strip()),
            stream=True,
        )
        yield (
            "done",
            {
                "response": accumulated.strip(),
                "request_id": request_id,
                "processing_time": round(latency_ms / 1000.0, 2),
                "usage": usage,
            },
        )
        return

    user_text = _user_text_from_messages(messages)
    workspace = os.getenv(
        "CURSOR_AGENT_WORKSPACE",
        os.getenv("OPRAI_INSTANCE_ROOT", "/home/opr"),
    ).strip()
    skill_block = load_matching_skill(workspace, user_text)
    thread, runtime = _build_agent_thread(
        messages,
        explore_mode=explore_mode,
        allow_writes=allow_writes,
        user_text=user_text,
        skill_block=skill_block,
    )
    tools = tool_schemas()
    max_tool_turns = max_agent_tool_turns()
    max_steps = int(os.getenv("INCEPTION_AGENT_MAX_STEPS", "12"))
    agent_temperature = resolve_temperature(agent_loop=True)
    tool_step_budget = max_tokens or agent_max_tokens(synthesis_step=False)
    steps = 0
    started = time.monotonic()
    final_text = ""

    for _ in range(max_steps):
        request_thread = trim_agent_thread(thread, max_tool_turns=max_tool_turns)
        synthesis_step = bool(request_thread) and request_thread[-1].get("role") == "tool"
        effort = resolve_reasoning_effort(
            agent_loop=True,
            explore_mode=explore_mode,
            allow_writes=allow_writes,
            tool_steps=steps,
            synthesis_step=synthesis_step,
        )
        step_max_tokens = agent_max_tokens(synthesis_step=synthesis_step)
        if not synthesis_step and max_tokens is not None:
            step_max_tokens = min(step_max_tokens, tool_step_budget)
        payload = {
            "model": target_model,
            "messages": request_thread,
            "max_tokens": step_max_tokens,
            "temperature": agent_temperature,
        }
        if synthesis_step:
            payload["tool_choice"] = "none"
        else:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(
            _api_extras(effort, synthesis_step=synthesis_step, streaming=synthesis_step)
        )

        if synthesis_step:
            step_started = time.monotonic()
            last_data = {}
            for event, value in _post_chat_stream(payload, per_call_timeout):
                if event == "error":
                    yield ("error", {"error": str(value), "request_id": request_id})
                    return
                if event == "delta":
                    final_text += str(value)
                    yield ("chunk", {"text": str(value)})
                elif event == "complete":
                    complete = value if isinstance(value, dict) else {}
                    final_text = str(complete.get("content") or final_text)
                    last_data = complete.get("data") if isinstance(complete.get("data"), dict) else {}
            step_latency_ms = int((time.monotonic() - step_started) * 1000)
            _log_inception_usage(
                model=target_model,
                data=last_data,
                latency_ms=step_latency_ms,
                success=bool(final_text.strip()),
                synthesis_step=True,
                tool_steps=steps,
                stream=True,
            )
            thread.append({"role": "assistant", "content": final_text})
            latency_ms = int((time.monotonic() - started) * 1000)
            yield (
                "done",
                {
                    "response": final_text.strip(),
                    "request_id": request_id,
                    "processing_time": round(latency_ms / 1000.0, 2),
                    "tool_steps": steps,
                },
            )
            return

        step_started = time.monotonic()
        status, data, err_text = _post_chat(payload, per_call_timeout)
        step_latency_ms = int((time.monotonic() - step_started) * 1000)
        if status != 200:
            _log_inception_usage(
                model=target_model,
                data={},
                latency_ms=step_latency_ms,
                success=False,
                error=err_text[:200],
                tool_steps=steps,
            )
            yield ("error", {"error": err_text, "request_id": request_id})
            return
        _log_inception_usage(
            model=target_model,
            data=data,
            latency_ms=step_latency_ms,
            success=True,
            tool_steps=steps,
        )

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {}) or {}
        tool_calls = message.get("tool_calls") or []
        content_raw = message.get("content")
        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": "" if content_raw is None else content_raw,
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        thread.append(assistant_msg)

        if not tool_calls:
            final_text = (message.get("content") or "").strip()
            if final_text:
                yield ("chunk", {"text": final_text})
            latency_ms = int((time.monotonic() - started) * 1000)
            yield (
                "done",
                {
                    "response": final_text,
                    "request_id": request_id,
                    "processing_time": round(latency_ms / 1000.0, 2),
                    "tool_steps": steps,
                },
            )
            return

        for tool_call in tool_calls:
            steps += 1
            fn = (tool_call.get("function") or {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            yield ("activity", {"line": f"tool: {name}"})
            result = execute_tool(name, args if isinstance(args, dict) else {}, runtime)
            thread.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", f"call_{steps}"),
                    "content": result,
                }
            )

    yield (
        "error",
        {
            "error": f"agent exceeded max steps ({max_steps})",
            "request_id": request_id,
            "tool_steps": steps,
        },
    )


def call_fim(
    prompt: str,
    suffix: str = "",
    model: Optional[str] = None,
    max_tokens: int = 256,
    timeout_seconds: int = 60,
) -> InceptionResult:
    """Mercury Edit 2 — v1/fim/completions (autocomplete / fill-in-middle)."""
    api_key = inception_api_key()
    if not api_key:
        return InceptionResult(
            success=False,
            content="",
            model=model or "",
            error="INCEPTION_API_KEY is not configured",
        )

    target_model = (model or os.getenv("INCEPTION_EDIT_MODEL", "mercury-edit-2")).strip()
    payload: Dict[str, Any] = {
        "model": target_model,
        "prompt": prompt,
        "max_tokens": max_tokens,
    }
    if suffix:
        payload["suffix"] = suffix

    started = time.monotonic()
    try:
        response = requests.post(
            inception_fim_url(),
            headers=_chat_headers(),
            json=payload,
            timeout=timeout_seconds,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return InceptionResult(
                success=False,
                content="",
                model=target_model,
                error=f"Inception FIM failed ({response.status_code}): {response.text[:500]}",
                latency_ms=latency_ms,
            )
        data = response.json()
        content = data.get("choices", [{}])[0].get("text", "") or ""
        return InceptionResult(
            success=True,
            content=content,
            model=target_model,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return InceptionResult(
            success=False,
            content="",
            model=target_model,
            error=str(exc),
            latency_ms=latency_ms,
        )


def check_health() -> Dict[str, Any]:
    api_key = inception_api_key()
    if not api_key:
        return {"ok": False, "error": "INCEPTION_API_KEY missing"}
    result = call_chat(
        messages=[{"role": "user", "content": "Reply with exactly: ok"}],
        max_tokens=16,
        temperature=0.5,
        reasoning_effort="instant",
        timeout_seconds=60,
    )
    return {
        "ok": result.success,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "preview": (result.content or "")[:120],
    }

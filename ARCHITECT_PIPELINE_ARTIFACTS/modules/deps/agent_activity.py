"""Live visibility for Cursor CLI agent runs (stream log + active run state)."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.instance_paths import instance_root_str
from modules.stream_text import stream_text_delta

_INSTANCE = instance_root_str()
DEFAULT_LOG = os.getenv(
    "OPRAI_AGENT_ACTIVITY_LOG",
    f"{_INSTANCE}/logs/agent_activity.log",
)
_LOCK = threading.Lock()
_ACTIVE: Optional[Dict[str, Any]] = None
_RUN_STATE: Dict[str, "_RunBuffers"] = {}
ACTIVE_FILE = "/tmp/oprai-agent-active.json"
_REQ_SHORT_LEN = 8
_TEXT_FLUSH_CHARS = int(os.getenv("OPRAI_AGENT_LOG_FLUSH_CHARS", "180"))
_WORD_BOUNDARY_RE = re.compile(r"[.!?…\s]$")


def _answer_stream_logging_enabled() -> bool:
    return os.getenv("OPRAI_AGENT_LOG_ANSWER_STREAM", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _word_boundary_flush_enabled() -> bool:
    return os.getenv("OPRAI_AGENT_LOG_WORD_BOUNDARY", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _at_word_boundary(text: str) -> bool:
    return bool(_WORD_BOUNDARY_RE.search(text))


def _think_max_chars() -> int:
    return int(os.getenv("OPRAI_AGENT_THINK_MAX_CHARS", "8000"))


def _answer_max_chars() -> int:
    return int(os.getenv("OPRAI_AGENT_ANSWER_MAX_CHARS", "4000"))


def activity_enabled() -> bool:
    return os.getenv("OPRAI_AGENT_ACTIVITY_ENABLED", "1").strip().lower() in ("1", "true", "yes")


def stream_json_enabled() -> bool:
    if not activity_enabled():
        return False
    return os.getenv("OPRAI_AGENT_STREAM_JSON", "1").strip().lower() in ("1", "true", "yes")


def log_path() -> Path:
    return Path(os.getenv("OPRAI_AGENT_ACTIVITY_LOG", DEFAULT_LOG))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _req_short(request_id: str) -> str:
    return (request_id or "?")[:_REQ_SHORT_LEN]


def _ensure_log_dir() -> None:
    log_path().parent.mkdir(parents=True, exist_ok=True)


def _write_log_line(line: str) -> None:
    _ensure_log_dir()
    with log_path().open("a", encoding="utf-8") as handle:
        handle.write(line)
        if not line.endswith("\n"):
            handle.write("\n")


def _write_active(data: Optional[Dict[str, Any]]) -> None:
    path = Path(ACTIVE_FILE)
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class _RunBuffers:
    text: str = ""
    accumulated: str = ""
    thinking: str = ""
    block_types: Dict[int, str] = field(default_factory=dict)
    logged_milestones: set[str] = field(default_factory=set)
    last_logged_answer: str = ""
    logged_prefix_len: int = 0
    read_tools: int = 0
    changes: int = 0


def _state(request_id: str) -> _RunBuffers:
    with _LOCK:
        if request_id not in _RUN_STATE:
            _RUN_STATE[request_id] = _RunBuffers()
        return _RUN_STATE[request_id]


def _clear_state(request_id: str) -> None:
    with _LOCK:
        _RUN_STATE.pop(request_id, None)


def get_buffer_preview(request_id: str) -> Dict[str, str]:
    """Live in-progress thinking/answer preview for API polling."""
    with _LOCK:
        state = _RUN_STATE.get(request_id)
        if state is None:
            return {"thinking": "", "answer": ""}
        return {
            "thinking": state.thinking[-500:],
            "answer": state.text[-500:],
        }


def get_active_buffers() -> Dict[str, Any]:
    """Buffers for the currently active request (if any)."""
    with _LOCK:
        if _ACTIVE is None:
            return {}
        rid = str(_ACTIVE.get("request_id") or "")
        if not rid:
            return {}
        state = _RUN_STATE.get(rid)
        if state is None:
            return {"request_id": rid, "thinking": "", "answer": ""}
        return {
            "request_id": rid,
            "thinking": state.thinking[-800:],
            "answer": state.text[-800:],
        }


def _log(request_id: str, tag: str, message: str) -> None:
    msg = message.strip()
    if not msg:
        return
    _write_log_line(f"{_now_iso()} [{tag:7}] req={_req_short(request_id)} | {msg}")


def log_remote_command(
    request_id: str,
    subcommand: str,
    *,
    duration_ms: int,
    ok: bool = True,
    detail: str = "",
    error: str = "",
) -> None:
    """Activity feed line for remote SSH / oprai-edge sub-agent calls (BR-11)."""
    if not activity_enabled():
        return
    cmd = (subcommand or "invoke").strip().lower()
    parts = [cmd]
    if detail:
        parts.append(detail.strip())
    parts.append(f"{max(0, int(duration_ms))}ms")
    parts.append("ok" if ok else "err")
    if error:
        parts.append(str(error).strip()[:200])
    rid = (request_id or os.getenv("OPRAI_REQUEST_ID") or "").strip()
    _log(rid or "?", "remote", " ".join(parts))


def log_remote_doctor(request_id: str, payload: Dict[str, Any]) -> None:
    """Compact doctor summary for activity feed after remote arm / manifest refresh."""
    if not activity_enabled():
        return
    ok = bool(payload.get("ok"))
    agent = str(payload.get("agent_id") or "").strip()
    disk = payload.get("disk_free_gb")
    bits = ["doctor", "ok" if ok else "fail"]
    if agent:
        bits.append(f"agent={agent}")
    if disk is not None:
        bits.append(f"disk={disk}GB")
    if not ok:
        err = str(payload.get("error") or "checks failed").strip()[:120]
        if err:
            bits.append(err)
    rid = (request_id or os.getenv("OPRAI_REQUEST_ID") or "").strip()
    _log(rid or "?", "remote", " ".join(bits))


def start_run(request_id: str, prompt_preview: str, pid: int) -> None:
    global _ACTIVE
    entry = {
        "request_id": request_id,
        "pid": pid,
        "prompt_preview": prompt_preview[:200],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    with _LOCK:
        _ACTIVE = dict(entry)
        _write_active(entry)
    preview = prompt_preview.replace("\n", " ").strip()
    if preview.lower().startswith("user:"):
        preview = preview[5:].strip()
    _log(request_id, "start", f"pid={pid} | {preview[:160]}")


def end_run(request_id: str, success: bool, latency_ms: Optional[int] = None, error: Optional[str] = None) -> None:
    global _ACTIVE
    state = _RUN_STATE.get(request_id)
    if success and state and state.read_tools > 5 and state.changes == 0:
        _log(
            request_id,
            "warn",
            f"run ended with {state.read_tools} read tools and zero [change] — deliverable may be missing",
        )
    _flush_pending_answer(request_id)
    _flush_thinking(request_id, force=True)
    status = "ok" if success else "error"
    tail = f"{status}"
    if latency_ms is not None:
        tail += f" {latency_ms}ms"
    if error:
        tail += f" | {error[:160]}"
    _log(request_id, "end", tail)
    _clear_state(request_id)
    with _LOCK:
        if _ACTIVE and _ACTIVE.get("request_id") == request_id:
            _ACTIVE = None
            _write_active(None)


def _flush_text(request_id: str, force: bool = False) -> None:
    if not force and not _answer_stream_logging_enabled():
        return
    state = _state(request_id)
    text = state.text.strip()
    if not text:
        state.text = ""
        return
    if not force and len(text) < 40:
        return
    if text == state.last_logged_answer:
        state.text = ""
        return
    logged = text[: _answer_max_chars()]
    _log(request_id, "answer", logged)
    state.last_logged_answer = logged
    state.logged_prefix_len = len(state.accumulated)
    state.text = ""


def _flush_pending_answer(request_id: str) -> None:
    """Log only the unlogged tail of accumulated text (end_run guard)."""
    state = _state(request_id)
    tail = state.accumulated[state.logged_prefix_len :].strip()
    if tail:
        logged = tail[: _answer_max_chars()]
        if logged != state.last_logged_answer:
            _log(request_id, "answer", logged)
            state.last_logged_answer = logged
        state.logged_prefix_len = len(state.accumulated)
    state.text = ""


def _flush_thinking(request_id: str, force: bool = False) -> None:
    state = _state(request_id)
    text = state.thinking.strip()
    if not text:
        state.thinking = ""
        return
    if not force and len(text) < 1:
        return
    _log(request_id, "think", text[: _think_max_chars()])
    state.thinking = ""


def _append_text(request_id: str, chunk: str) -> None:
    if not chunk:
        return
    state = _state(request_id)
    delta = stream_text_delta(state.accumulated, chunk)
    if not delta:
        return
    state.accumulated += delta
    state.text += delta
    if "\n" in delta:
        _flush_text(request_id, force=True)
        return
    if len(state.text) >= _TEXT_FLUSH_CHARS:
        if not _word_boundary_flush_enabled() or _at_word_boundary(state.text):
            _flush_text(request_id, force=True)


def _append_thinking(request_id: str, chunk: str) -> None:
    if not chunk:
        return
    state = _state(request_id)
    state.thinking += chunk


def stream_line(request_id: str, channel: str, line: str) -> None:
    text = line.rstrip("\n")
    if not text:
        return
    if channel == "stderr":
        _log(request_id, "stderr", text[:500])
        return
    event = parse_stream_event(text, request_id=request_id)
    if event is None:
        _append_text(request_id, text)
        return
    _handle_event(request_id, event)


@dataclass
class StreamEvent:
    kind: str
    text: str = ""
    tool: str = ""
    change: str = ""
    blocker: str = ""
    milestone: str = ""
    block_index: Optional[int] = None
    block_type: str = ""


def _block_type_from_content(content_block: dict) -> str:
    block_type = str(content_block.get("type") or "")
    if block_type in {"thinking", "reasoning"}:
        return "thinking"
    if block_type in {"tool_use", "tool_call", "tool"}:
        return "tool"
    return "text"


def _format_tool_detail(name: str, tool_input: Any) -> str:
    if not name:
        name = "tool"
    if tool_input is None:
        return name
    if isinstance(tool_input, dict):
        parts: List[str] = [name]
        path = tool_input.get("path") or tool_input.get("file")
        if isinstance(path, str) and path.strip():
            parts.append(f"path={path.strip()[:160]}")
        for key in ("old_string", "new_string", "command", "query", "pattern", "streamContent"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                preview = value.strip().replace("\n", "\\n")[:160]
                parts.append(f"{key}={preview}")
                break
        if len(parts) == 1:
            compact = json.dumps(tool_input, ensure_ascii=False)
            if compact and compact != "{}":
                parts.append(compact[:200])
        return " ".join(parts)
    return f"{name} {str(tool_input)[:200]}"


_CURSOR_TOOL_KEYS = {
    "readToolCall": "Read",
    "editToolCall": "StrReplace",
    "writeToolCall": "Write",
    "shellToolCall": "Shell",
    "grepToolCall": "Grep",
    "globToolCall": "Glob",
    "listToolCall": "LS",
    "deleteToolCall": "Delete",
    "searchToolCall": "SemanticSearch",
}


def _parse_cursor_tool_call(tool_call: Any) -> tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
    if not isinstance(tool_call, dict):
        return "", {}, None
    for key, name in _CURSOR_TOOL_KEYS.items():
        block = tool_call.get(key)
        if isinstance(block, dict):
            args = block.get("args") if isinstance(block.get("args"), dict) else {}
            result = block.get("result") if isinstance(block.get("result"), dict) else None
            return name, args, result
    return "", {}, None


def _change_is_blocked(args: Dict[str, Any], result: Optional[Dict[str, Any]], formatted: str) -> bool:
    if isinstance(result, dict):
        success = result.get("success")
        if isinstance(success, dict):
            if success.get("success") is False:
                return True
            msg = str(success.get("message") or "").lower()
            if any(token in msg for token in ("denied", "blocked", "not allowed", "permission")):
                return True
        err = str(result.get("error") or "").lower()
        if err and any(token in err for token in ("denied", "blocked", "not allowed", "permission")):
            return True
    path = str(args.get("path") or "").strip()
    if not path and formatted.startswith("path=?"):
        return True
    if formatted == "path=?" or formatted.startswith("path=? | pending"):
        return bool(str(args.get("streamContent") or args.get("new_string") or "").strip())
    return False


def _format_edit_change(args: Dict[str, Any], result: Optional[Dict[str, Any]]) -> str:
    path = str(args.get("path") or "").strip()
    head = f"path={path[:160]}" if path else "path=?"
    if not isinstance(result, dict):
        preview = args.get("streamContent") or args.get("new_string") or ""
        if isinstance(preview, str) and preview.strip():
            return f"{head} | pending {preview.strip().replace(chr(10), '\\n')[:120]}"
        return head
    success = result.get("success")
    if not isinstance(success, dict):
        return head
    added = success.get("linesAdded")
    removed = success.get("linesRemoved")
    stats = ""
    if added is not None or removed is not None:
        stats = f" | +{added or 0} -{removed or 0}"
    diff = success.get("diffString")
    if isinstance(diff, str) and diff.strip():
        diff_lines = [ln for ln in diff.strip().splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
        if diff_lines:
            snippet = " ".join(ln[:80] for ln in diff_lines[:4])
            return f"{head}{stats} | {snippet[:300]}"
    msg = success.get("message")
    if isinstance(msg, str) and msg.strip():
        return f"{head}{stats} | {msg.strip()[:200]}"
    return f"{head}{stats}"


def _register_block(request_id: str, index: int, block_type: str) -> None:
    state = _state(request_id)
    state.block_types[index] = block_type
    milestone = f"block/{index}/{block_type}"
    if milestone in state.logged_milestones:
        return
    state.logged_milestones.add(milestone)
    if block_type == "thinking":
        _log(request_id, "phase", f"thinking block {index} start")


def _unregister_block(request_id: str, index: int, block_type: str) -> None:
    state = _state(request_id)
    state.block_types.pop(index, None)
    milestone = f"block_stop/{index}/{block_type}"
    if milestone in state.logged_milestones:
        return
    state.logged_milestones.add(milestone)
    if block_type == "thinking":
        _flush_thinking(request_id, force=True)
        _log(request_id, "phase", f"thinking block {index} stop")


def parse_stream_event(line: str, request_id: str = "") -> Optional[StreamEvent]:
    """Parse one NDJSON line from cursor agent stream-json."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    event_type = str(obj.get("type") or obj.get("event") or "")
    subtype = str(obj.get("subtype") or "")

    if event_type == "result":
        if subtype == "success":
            return StreamEvent(kind="milestone", milestone="done")
        return StreamEvent(kind="milestone", milestone=f"result/{subtype or 'unknown'}")

    if event_type in {"system", "user"} and subtype:
        return StreamEvent(kind="milestone", milestone=f"{event_type}/{subtype}")

    if event_type in {"thinking", "reasoning"}:
        text = _extract_text(obj)
        if subtype == "completed":
            return StreamEvent(kind="thinking_done")
        if text:
            return StreamEvent(kind="thinking", text=text)

    if event_type == "content_block_start":
        content_block = obj.get("content_block")
        if isinstance(content_block, dict):
            index = obj.get("index")
            block_type = _block_type_from_content(content_block)
            if isinstance(index, int):
                if request_id:
                    _register_block(request_id, index, block_type)
            if block_type == "tool":
                name = _extract_tool_name(obj)
                detail = _format_tool_detail(name, content_block.get("input"))
                return StreamEvent(kind="tool", tool=detail, block_index=index if isinstance(index, int) else None)
            return StreamEvent(kind="noop")
        tool = _extract_tool_name(obj)
        if tool:
            return StreamEvent(kind="tool", tool=tool)
        return StreamEvent(kind="noop")

    if event_type == "content_block_stop":
        content_block = obj.get("content_block")
        index = obj.get("index")
        if isinstance(index, int) and request_id:
            state = _state(request_id)
            block_type = state.block_types.get(index, "")
            if isinstance(content_block, dict) and not block_type:
                block_type = _block_type_from_content(content_block)
            if block_type:
                _unregister_block(request_id, index, block_type)
        return StreamEvent(kind="noop")

    if event_type in {"content_block_delta", "text_delta", "text"}:
        delta = obj.get("delta")
        if isinstance(delta, dict):
            thinking = delta.get("thinking")
            if isinstance(thinking, str) and thinking:
                return StreamEvent(kind="thinking", text=thinking)
            text = delta.get("text") or delta.get("partial_text") or delta.get("content")
            if isinstance(text, str) and text:
                index = obj.get("index")
                if isinstance(index, int) and request_id:
                    block_type = _state(request_id).block_types.get(index, "text")
                    if block_type == "thinking":
                        return StreamEvent(kind="thinking", text=text)
                return StreamEvent(kind="text", text=text)
        text = _extract_text(obj)
        if text:
            return StreamEvent(kind="text", text=text)

    if event_type in {"tool_call", "tool_use", "tool"}:
        tool_call = obj.get("tool_call")
        name, args, result = _parse_cursor_tool_call(tool_call)
        if name:
            if subtype == "completed" and name in {"StrReplace", "Write", "Delete"}:
                change = _format_edit_change(args, result)
                if _change_is_blocked(args, result, change):
                    return StreamEvent(kind="blocker", blocker=f"write blocked: {change}")
                return StreamEvent(kind="change", change=change)
            if subtype == "completed" and name == "Shell":
                detail = _format_tool_detail(name, args)
                if isinstance(result, dict):
                    success = result.get("success")
                    if isinstance(success, dict) and success.get("success") is False:
                        msg = str(success.get("message") or success.get("stderr") or detail)
                        return StreamEvent(kind="blocker", blocker=f"shell blocked: {msg[:300]}")
                    err = str(result.get("error") or "").lower()
                    if err and any(t in err for t in ("denied", "blocked", "not allowed")):
                        return StreamEvent(kind="blocker", blocker=f"shell blocked: {detail[:300]}")
                return StreamEvent(kind="noop")
            if subtype in {"", "started", "start"}:
                return StreamEvent(kind="tool", tool=_format_tool_detail(name, args))
            if subtype == "completed" and name in {"Read", "Grep", "Glob", "LS", "SemanticSearch"}:
                return StreamEvent(kind="noop")
            if subtype == "completed":
                detail = _format_tool_detail(name, args)
                if isinstance(result, dict):
                    success = result.get("success")
                    if isinstance(success, dict):
                        msg = success.get("message") or success.get("content")
                        if isinstance(msg, str) and msg.strip():
                            detail = f"{detail} | done"
                return StreamEvent(kind="tool", tool=detail)
        name = _extract_tool_name(obj)
        tool_input = obj.get("input") or obj.get("arguments")
        if name:
            return StreamEvent(kind="tool", tool=_format_tool_detail(name, tool_input))

    if event_type == "assistant":
        text = _extract_assistant_text(obj)
        if text:
            return StreamEvent(kind="text", text=text)

    if subtype:
        key = f"{event_type}/{subtype}"
        return StreamEvent(kind="milestone", milestone=key)

    if event_type:
        return StreamEvent(kind="milestone", milestone=event_type)

    return None


def _extract_text(obj: dict) -> str:
    for key in ("text", "content", "message"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_assistant_text(obj: dict) -> str:
    message = obj.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _extract_tool_name(obj: dict) -> str:
    for key in ("name", "tool_name", "tool"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for nested_key in ("tool_call", "tool_use", "content_block", "block", "item"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            for key, name in _CURSOR_TOOL_KEYS.items():
                if key in nested:
                    return name
            name = _extract_tool_name(nested)
            if name:
                return name

    delta = obj.get("delta")
    if isinstance(delta, dict):
        name = delta.get("name") or delta.get("tool_name")
        if isinstance(name, str) and name.strip():
            return name.strip()

    return ""


def _handle_event(request_id: str, event: StreamEvent) -> None:
    if event.kind == "noop":
        return

    if event.kind == "milestone":
        state = _state(request_id)
        label = event.milestone
        if label in state.logged_milestones:
            return
        state.logged_milestones.add(label)
        if label in {"system/init", "user"}:
            _log(request_id, "phase", label.replace("/", " "))
        elif label == "done":
            _flush_thinking(request_id, force=True)
            _flush_text(request_id, force=True)
        return

    if event.kind == "thinking":
        _append_thinking(request_id, event.text)
        return

    if event.kind == "thinking_done":
        _flush_thinking(request_id, force=True)
        return

    if event.kind == "tool":
        _flush_thinking(request_id, force=True)
        _flush_text(request_id, force=True)
        state = _state(request_id)
        tool_line = (event.tool or "").lower()
        if tool_line.startswith("read") or " grep " in f" {tool_line} " or tool_line.startswith("glob"):
            state.read_tools += 1
        _log(request_id, "tool", event.tool)
        return

    if event.kind == "change":
        _flush_thinking(request_id, force=True)
        _flush_text(request_id, force=True)
        _state(request_id).changes += 1
        _log(request_id, "change", event.change)
        return

    if event.kind == "blocker":
        _flush_thinking(request_id, force=True)
        _flush_text(request_id, force=True)
        _log(request_id, "blocker", event.blocker)
        return

    if event.kind == "text":
        _append_text(request_id, event.text)
        return


def format_stream_event(line: str, request_id: str = "") -> str:
    """Backward-compatible helper for tests and plain-text fallback."""
    event = parse_stream_event(line, request_id)
    if event is None:
        return line[:300]
    if event.kind == "thinking":
        return f"… thinking: {event.text[:120]}"
    if event.kind == "tool":
        return f"→ tool: {event.tool}"
    if event.kind == "change":
        return f"✎ change: {event.change}"
    if event.kind == "blocker":
        return f"⛔ blocker: {event.blocker}"
    if event.kind == "text":
        return event.text[:300]
    if event.milestone:
        return f"({event.milestone})"
    return ""


def _text_from_stream_line(line: str, request_id: str = "") -> str:
    event = parse_stream_event(line, request_id)
    if event is not None:
        if event.kind != "text":
            return ""
        return event.text or ""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(obj, dict):
        return ""
    event_type = str(obj.get("type") or obj.get("event") or "")
    if event_type in {"thinking", "reasoning"}:
        return ""
    delta = obj.get("delta")
    if isinstance(delta, dict):
        if delta.get("thinking"):
            return ""
        text = delta.get("text") or delta.get("partial_text")
        if isinstance(text, str) and text:
            index = obj.get("index")
            if isinstance(index, int) and request_id:
                block_type = _state(request_id).block_types.get(index, "text")
                if block_type == "thinking":
                    return ""
            return text
    text = _extract_assistant_text(obj) or _extract_text(obj)
    if text and event_type_is_not_thinking_delta(obj):
        return text
    result = obj.get("result")
    if isinstance(result, str) and result.strip():
        return result
    return ""


def parse_stream_json_output(stdout: str, request_id: str = "") -> str:
    """Rebuild final assistant text from stream-json lines."""
    accumulated = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        incoming = _text_from_stream_line(line, request_id)
        if not incoming:
            continue
        delta = stream_text_delta(accumulated, incoming)
        if delta:
            accumulated += delta
    return accumulated.strip()


def event_type_is_not_thinking_delta(obj: dict) -> bool:
    delta = obj.get("delta")
    if isinstance(delta, dict) and delta.get("thinking"):
        return False
    return True


_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\s*\] req=(\S+) \| (.*)$"
)
_THINK_TAGS = frozenset({"think", "thinking"})


def _line_matches_filters(
    line: str,
    *,
    request_id: Optional[str] = None,
    hide_think: bool = False,
    tags: Optional[set[str]] = None,
) -> bool:
    match = _LOG_LINE_RE.match(line.strip())
    if not match:
        return request_id is None and not hide_think and tags is None
    _ts, tag, req_token, _msg = match.groups()
    tag_norm = tag.strip().lower()
    if hide_think and tag_norm in _THINK_TAGS:
        return False
    if tags is not None and tag_norm not in tags:
        return False
    if request_id:
        rid = request_id.strip().lower()
        req = req_token.strip().lower()
        if not rid.startswith(req) and not req.startswith(rid[: _REQ_SHORT_LEN]):
            return False
    return True


def tail_log(
    max_lines: int = 80,
    *,
    request_id: Optional[str] = None,
    hide_think: bool = False,
    tags: Optional[set[str]] = None,
) -> List[str]:
    path = log_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if request_id or hide_think or tags:
        lines = [
            line
            for line in lines
            if _line_matches_filters(
                line,
                request_id=request_id,
                hide_think=hide_think,
                tags=tags,
            )
        ]
    return lines[-max_lines:]


def get_activity_summary() -> Dict[str, Any]:
    """Lightweight log tail stats for /api/agent/activity/summary."""
    path = log_path()
    if not path.is_file():
        return {"lines": 0, "last_event": ""}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": len(lines), "last_event": lines[-1] if lines else ""}


def is_agent_running() -> bool:
    import subprocess

    try:
        result = subprocess.run(
            ["pgrep", "-f", "cursor-cli.sh agent"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def get_snapshot(
    tail_lines: int = 80,
    *,
    request_id: Optional[str] = None,
    hide_think: bool = False,
) -> Dict[str, Any]:
    active: Optional[Dict[str, Any]] = None
    active_path = Path(ACTIVE_FILE)
    if active_path.is_file():
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            active = None

    running = is_agent_running()
    buffers = dict(get_active_buffers())
    if hide_think:
        buffers.pop("thinking", None)
    if request_id:
        rid = request_id.strip().lower()
        buf_rid = str(buffers.get("request_id") or "").strip().lower()
        if buf_rid and not rid.startswith(buf_rid) and not buf_rid.startswith(rid[: _REQ_SHORT_LEN]):
            buffers = {}

    return {
        "enabled": activity_enabled(),
        "stream_json": stream_json_enabled(),
        "log_path": str(log_path()),
        "running": running,
        "active": active,
        "buffers": buffers,
        "tail": tail_log(
            tail_lines,
            request_id=request_id,
            hide_think=hide_think,
        ),
        "filters": {
            "request_id": request_id or "",
            "hide_think": hide_think,
        },
        "watch_command": f"bash {_INSTANCE}/scripts/watch-agent.sh --follow",
        "summary_command": f"bash {_INSTANCE}/scripts/watch-agent.sh --summary",
    }

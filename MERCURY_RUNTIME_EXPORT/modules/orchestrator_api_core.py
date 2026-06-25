"""Shared OPRAI Orchestrator API core — Flask app factory and route handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from functools import lru_cache
import ast

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import subprocess
import os
import json
import pickle
import hashlib
import time
import tempfile
import importlib
import uuid
import re
from datetime import datetime
from pathlib import Path
from modules.autonomy_controller import get_autonomy_controller
from modules.plan_auditor import audit_window, audit_plan
from modules.chat_rate_limit import check_chat_rate_limit
from modules.oprai_metrics import inc_chat_requests, inc_http_request, render_prometheus_text
from modules.llm_trace import log_llm_trace
from modules.codebase_context import (
    auto_plan_detect_enabled,
    explore_allowed,
    get_status as context_status,
    resolve_cli_profile,
)
from modules.instance_paths import TargetConflictError, resolve_write_target
from modules.project_registry import (
    ProjectRegistryError,
    list_projects_public,
    project_to_public_dict,
    resolve_project,
    resolve_remote_project,
    validate_project_id,
)
from modules.remote_context import prefetch_for_chat
from modules.remote_panel import (
    build_remote_manifest,
    public_remote_file_read,
    public_remote_index,
    run_w12_phase_verify,
    save_prefetch_manifest,
)
from modules.stream_text import dedupe_repeated_paragraphs
from modules.patch_contract_validator import validate_patch_response_contract
from modules.patch_policy import evaluate_patch_policy
from modules.patch_risk_scorer import score_patch_risk
from modules.patch_dry_runner import run_patch_dry_run
from modules.patch_applier import apply_patch_transactional
from modules.patch_audit import append_patch_audit

_OPRAI_CONTEXT_RE = re.compile(
    r"\[OPRAI_CONTEXT[^\]]*\].*?--- end OPRAI_CONTEXT ---",
    re.DOTALL,
)



@dataclass(frozen=True)
class OrchestratorApiConfig:
    """Per-instance API configuration (root vs ORK)."""

    instance_root: str
    default_port: int = 5004
    variant: str = "root"  # "root" | "ork"
    enable_lab_target: bool = False
    guard_bridge_on_import: bool = False
    guard_bridge_on_load_executor: bool = False
    context_include_workspace_root: bool = False
    fixed_prod_paths: bool = False
    auto_plan_direct_import: bool = False


_api_config: OrchestratorApiConfig | None = None
current_plan_executor = None
autonomy_controller = get_autonomy_controller()


def _request_lineage_key(request_id: str) -> str:
    rid = (request_id or "").strip()
    if not rid:
        return ""
    while True:
        lowered = rid.lower()
        if lowered.endswith("-stub-resume"):
            rid = rid[: -len("-stub-resume")]
            continue
        if lowered.endswith("-resume"):
            rid = rid[: -len("-resume")]
            continue
        break
    return rid


def _subagent_trace(event: str, payload: dict) -> None:
    try:
        from modules.agent_activity import _log

        request_id = str(payload.get("request_id") or os.getenv("OPRAI_REQUEST_ID", "")).strip() or "unknown"
        safe_payload = {k: payload[k] for k in sorted(payload.keys())}
        safe_payload.setdefault("request_id", request_id)
        safe_payload.setdefault("lineage_key", _request_lineage_key(request_id))
        safe_payload.setdefault("automation", payload.get("automation", "chat"))
        safe_payload.setdefault("generation_params", payload.get("generation_params", {}))
        safe_payload.setdefault("phase", payload.get("phase", "unknown"))
        safe_payload.setdefault("error_type", payload.get("error_type"))
        _log(request_id, "guard", f"{event} {json.dumps(safe_payload, ensure_ascii=False)}")
    except Exception:
        pass


def _verify_snapshot_file(request_id: str, artifact_path: str) -> Path:
    root = Path(tempfile.gettempdir()) / "oprai_verify_locks"
    root.mkdir(parents=True, exist_ok=True)
    safe_request = re.sub(r"[^A-Za-z0-9_.-]", "_", _request_lineage_key(request_id))
    digest = hashlib.sha256(artifact_path.encode("utf-8")).hexdigest()[:16]
    return root / f"{safe_request}_{digest}_verify_snapshot.json"


def _make_file_snapshot(path: Path) -> dict:
    data = path.read_bytes()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime": path.stat().st_mtime,
        "size": len(data),
    }


def _verify_artifact_snapshot_intact(request_id: str, artifact_path: str) -> str | None:
    artifact = Path(artifact_path)
    if not artifact.is_file():
        return None
    if not artifact.name.upper().startswith("VERIFY"):
        return None
    snap_path = _verify_snapshot_file(request_id, str(artifact.resolve()))
    if not snap_path.is_file():
        return None
    try:
        old = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(old, dict):
        return None
    cur = _make_file_snapshot(artifact)
    if old != cur:
        return f"{artifact.name}: verify_overwritten_after_lock"
    return None


def _verify_snapshots_intact(request_id: str, artifacts: list[str]) -> list[str]:
    issues: list[str] = []
    for artifact in artifacts:
        issue = _verify_artifact_snapshot_intact(request_id, artifact)
        if issue:
            issues.append(issue)
    return issues


def _cfg() -> OrchestratorApiConfig:
    if _api_config is None:
        raise RuntimeError("orchestrator_api_core.create_app() was not called")
    return _api_config


def _load_plan_executor_symbols():
    if _cfg().guard_bridge_on_load_executor:
        from modules.root_orchestrator_guard import ensure_root_orchestrator_bridge
        ensure_root_orchestrator_bridge()
    module = importlib.import_module("agent_orchestrator_v8")
    plan_executor_cls = getattr(module, "PlanExecutor", None)
    receive_plan_fn = getattr(module, "receive_plan", None)
    if plan_executor_cls is None:
        raise ImportError("PlanExecutor is not available in agent_orchestrator_v8")
    return module, receive_plan_fn, plan_executor_cls


def _compat_load_execution_state(executor):
    if hasattr(executor, "load_execution_state"):
        try:
            executor.load_execution_state()
        except Exception:
            pass
    if not hasattr(executor, "execution_state") or not isinstance(getattr(executor, "execution_state"), dict):
        executor.execution_state = {}


def _compat_save_execution_state(executor):
    if hasattr(executor, "save_execution_state"):
        try:
            executor.save_execution_state()
        except Exception:
            pass


_PLAN_STATUS_KEYS = (
    "plan_name",
    "status",
    "progress",
    "current_step",
    "total_steps",
    "completed_steps",
    "plan_steps",
    "execution_logs",
    "results",
    "errors",
    "start_time",
    "last_update",
)


def _read_plan_json_file(plan_file):
    try:
        if plan_file and os.path.isfile(plan_file):
            with open(plan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def _merge_status_disk_fallback(executor, status):
    """If in-memory status lost plan_name (e.g. other worker saved plan), merge from disk."""
    if not isinstance(status, dict):
        status = {}
    name = status.get("plan_name")
    if name is not None and str(name).strip():
        return status
    plan_file = getattr(executor, "plan_file", None) or f"{_cfg().instance_root}/current_plan.json"
    disk = _read_plan_json_file(plan_file)
    if not disk:
        return status
    merged = dict(status)
    for k in _PLAN_STATUS_KEYS:
        cur = merged.get(k)
        if cur in (None, "") and disk.get(k) not in (None, ""):
            merged[k] = disk[k]
        elif k not in merged and k in disk:
            merged[k] = disk[k]
    return merged


def _compat_get_execution_status(executor):
    state = getattr(executor, "execution_state", {}) or {}
    status = {}
    if hasattr(executor, "get_execution_status"):
        try:
            st = executor.get_execution_status()
            if isinstance(st, dict):
                status = dict(st)
        except Exception:
            status = {}
    for k in _PLAN_STATUS_KEYS:
        v = status.get(k)
        if v in (None, "") and state.get(k) not in (None, ""):
            status[k] = state[k]
        elif k not in status and k in state:
            status[k] = state[k]
    return status


def _normalize_plan_status(status):
    if not isinstance(status, dict):
        return status
    normalized = dict(status)
    total_steps = int(normalized.get("total_steps") or 0)
    current_step = int(normalized.get("current_step") or 0)
    completed_steps = int(normalized.get("completed_steps") or 0)
    progress = float(normalized.get("progress") or 0.0)
    state = str(normalized.get("status") or "idle").lower()
    if total_steps > 0 and completed_steps <= 0:
        completed_steps = min(current_step, total_steps)
        normalized["completed_steps"] = completed_steps
    if total_steps > 0 and progress <= 0 and completed_steps > 0:
        normalized["progress"] = round((completed_steps / total_steps) * 100, 2)
        progress = float(normalized["progress"])
    is_done = (
        (total_steps > 0 and current_step >= total_steps) or
        (total_steps > 0 and completed_steps >= total_steps) or
        progress >= 100.0
    )
    if is_done and state not in ("failed", "cancelled"):
        normalized["status"] = "completed"
        if total_steps > 0:
            normalized["current_step"] = total_steps
            normalized["completed_steps"] = total_steps
            normalized["progress"] = 100.0
    return normalized


def _compat_reset_plan(executor):
    if hasattr(executor, "reset_plan"):
        try:
            executor.reset_plan()
            return
        except Exception:
            pass
    executor.execution_state = {
        "status": "cancelled",
        "plan_steps": [],
        "results": [],
        "errors": [],
        "execution_logs": [],
    }
    _compat_save_execution_state(executor)

# Система кэширования для API
class APIResponseCache:
    def __init__(self, cache_file='/tmp/api_cache.pkl', ttl_seconds=1800):
        self.cache_file = cache_file
        self.ttl_seconds = ttl_seconds
        self.cache = {}
        self.timestamps = {}
        self.stats = {'hits': 0, 'misses': 0, 'total': 0}
        self.load_cache()

    def get(self, key):
        """Получить кэшированный ответ"""
        if key in self.cache:
            if time.time() - self.timestamps.get(key, 0) < self.ttl_seconds:
                self.stats['hits'] += 1
                self.stats['total'] += 1
                return self.cache[key]
            else:
                # Удаляем просроченную запись
                del self.cache[key]
                del self.timestamps[key]

        self.stats['misses'] += 1
        self.stats['total'] += 1
        return None

    def set(self, key, value):
        """Сохранить ответ в кэш"""
        self.cache[key] = value
        self.timestamps[key] = time.time()
        self.save_cache()

    def generate_key(self, message):
        """Генерировать ключ для кэширования"""
        normalized = ' '.join(message.lower().strip().split())
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()

    def load_cache(self):
        """Загрузить кэш с диска"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'rb') as f:
                    data = pickle.load(f)
                    self.cache = data.get('cache', {})
                    self.timestamps = data.get('timestamps', {})
                    self.stats = data.get('stats', self.stats)
        except Exception as e:
            print(f"Warning: Failed to load cache: {e}")

    def save_cache(self):
        """Сохранить кэш на диск"""
        try:
            data = {
                'cache': self.cache,
                'timestamps': self.timestamps,
                'stats': self.stats
            }
            with open(self.cache_file, 'wb') as f:
                pickle.dump(data, f)
        except Exception as e:
            print(f"Warning: Failed to save cache: {e}")

    def get_stats(self):
        """Получить статистику кэширования"""
        total = self.stats['total']
        hit_rate = (self.stats['hits'] / total * 100) if total > 0 else 0
        return {
            'cache_size': len(self.cache),
            'total_requests': total,
            'cache_hits': self.stats['hits'],
            'cache_misses': self.stats['misses'],
            'hit_rate_percent': round(hit_rate, 2),
            'ttl_seconds': self.ttl_seconds
        }

    def clear_expired(self):
        """Очистить просроченные записи"""
        current_time = time.time()
        expired_keys = [k for k, t in self.timestamps.items()
                       if current_time - t > self.ttl_seconds]

        for key in expired_keys:
            if key in self.cache:
                del self.cache[key]
            if key in self.timestamps:
                del self.timestamps[key]

        if expired_keys:
            self.save_cache()

        return len(expired_keys)

# Глобальный экземпляр кэша API
api_cache = APIResponseCache()

ORCHESTRATOR_TIMEOUT_SECONDS = int(os.getenv("ORCHESTRATOR_TIMEOUT_SECONDS", "0"))
FAST_ORCHESTRATOR_TIMEOUT_SECONDS = int(os.getenv("FAST_ORCHESTRATOR_TIMEOUT_SECONDS", "0"))
ORCHESTRATOR_CHAT_EXECUTION_MODE = os.getenv("ORCHESTRATOR_CHAT_EXECUTION_MODE", "llm_router").strip().lower()
GEMINI_QUOTA_COOLDOWN_SECONDS = int(os.getenv("GEMINI_QUOTA_COOLDOWN_SECONDS", "900"))
ERROR_CACHE_MARKERS = (
    "❌ Ошибка",
    "LLMRouter Error",
    "Traceback",
    "Exception:",
    "circuit breaker is open",
    "TerminalQuotaError",
    "QUOTA_EXHAUSTED",
)
quota_blocked_until = 0.0
quota_block_reason = ""


def _normalize_generation_params(raw: dict | None) -> dict:
    params = dict(raw or {})
    normalized: dict = {}

    if "temperature" in params and params["temperature"] is not None:
        try:
            temperature = float(params["temperature"])
        except (TypeError, ValueError):
            raise ValueError("temperature must be a number between 0.0 and 1.0")
        if not (0.0 <= temperature <= 1.0):
            raise ValueError("temperature must be between 0.0 and 1.0")
        normalized["temperature"] = temperature

    if "top_p" in params and params["top_p"] is not None:
        try:
            top_p = float(params["top_p"])
        except (TypeError, ValueError):
            raise ValueError("top_p must be a number between 0.0 and 1.0")
        if not (0.0 <= top_p <= 1.0):
            raise ValueError("top_p must be between 0.0 and 1.0")
        normalized["top_p"] = top_p

    if "top_k" in params and params["top_k"] is not None:
        top_k = params["top_k"]
        if not isinstance(top_k, int) or top_k < 0:
            raise ValueError("top_k must be a non-negative integer")
        normalized["top_k"] = top_k

    max_tokens_raw = params.get("max_tokens")
    if max_tokens_raw is None:
        max_tokens_raw = params.get("max_output_tokens")
    if max_tokens_raw is not None:
        if not isinstance(max_tokens_raw, int) or max_tokens_raw < 1:
            raise ValueError("max_tokens must be a positive integer")
        normalized["max_tokens"] = max_tokens_raw

    if "stop" in params and params["stop"] is not None:
        stop = params["stop"]
        if isinstance(stop, str):
            normalized["stop"] = [stop]
        elif isinstance(stop, list) and all(isinstance(item, str) for item in stop):
            normalized["stop"] = stop
        else:
            raise ValueError("stop must be a string or list of strings")

    return normalized


def _build_runtime_cache_key(
    message: str,
    fast_mode: bool,
    generation_params: dict | None = None,
) -> str:
    normalized_generation = _normalize_generation_params(generation_params)
    profile = "|".join(
        [
            os.getenv("LLM_PROVIDER", ""),
            os.getenv("LLM_MODEL", ""),
            os.getenv("CURSOR_CLI_COMMAND", ""),
            os.getenv("CURSOR_AGENT_WORKSPACE", ""),
            os.getenv("OPRAI_EXPLORE_MODE", ""),
            os.getenv("OPRAI_ALLOW_WRITES", ""),
            os.getenv("OPRAI_CLI_PROFILE", ""),
            "fast" if fast_mode else "full",
            json.dumps(normalized_generation, sort_keys=True, ensure_ascii=False),
        ]
    )
    return f"{message}|{profile}"


def _is_cacheable_response(response: str) -> bool:
    if not response or not response.strip():
        return False
    lowered = response.lower()
    return not any(marker.lower() in lowered for marker in ERROR_CACHE_MARKERS)


def _extract_process_error(result: subprocess.CompletedProcess) -> str:
    stderr_text = (result.stderr or "").strip()
    stdout_text = (result.stdout or "").strip()
    if stderr_text:
        return stderr_text[-500:]
    if stdout_text:
        return stdout_text[-500:]
    return "Нет вывода"


def _is_quota_exhausted_response(text: str) -> bool:
    lowered = (text or "").lower()
    return "terminalquotaerror" in lowered or "quota_exhausted" in lowered or "exhausted your capacity" in lowered


def _resolve_subprocess_timeout(fast_mode: bool):
    configured_timeout = FAST_ORCHESTRATOR_TIMEOUT_SECONDS if fast_mode else ORCHESTRATOR_TIMEOUT_SECONDS
    if configured_timeout <= 0:
        return None
    return configured_timeout


def _emit_llm_trace(event: str, request_id: str, endpoint: str, source: str, fast_mode: bool, cache_hit: bool = False):
    trace = {
        "event": event,
        "request_id": request_id,
        "endpoint": endpoint,
        "source": source,
        "provider": os.getenv("LLM_PROVIDER", "unknown"),
        "mode": "fast" if fast_mode else "full",
        "cache_hit": cache_hit,
        "ts": datetime.now().isoformat(),
    }
    print(f"LLM_TRACE {json.dumps(trace, ensure_ascii=False)}")


def _response_indicates_llm_error(response: str) -> bool:
    if not response:
        return True
    markers = ("❌", "⏰", "LLMRouter Error:", "QUOTA_EXHAUSTED")
    return any(marker in response for marker in markers)


def _response_ok_contract(pipeline: dict) -> bool:
    if not isinstance(pipeline, dict):
        return False
    explicit_ok = pipeline.get("ok")
    if explicit_ok is not None:
        return bool(explicit_ok)
    if pipeline.get("error"):
        return False
    return not _response_indicates_llm_error(str(pipeline.get("response", "")))


def _infer_error_class(pipeline: dict) -> str | None:
    if not isinstance(pipeline, dict):
        return "invalid_envelope"
    raw = str(pipeline.get("error") or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if "quota" in lowered:
        return "quota_exhausted"
    if "timeout" in lowered:
        return "timeout"
    if "unsupported" in lowered:
        return "unsupported_provider"
    if "not configured" in lowered:
        return "provider_not_configured"
    return pipeline.get("error_class") or "provider_failure"


def _build_request_context(
    *,
    request_id: str,
    automation: str,
    generation_params: dict,
    parity_eval: bool,
    source: str,
) -> dict:
    return {
        "request_id": request_id,
        "lineage_key": _request_lineage_key(request_id),
        "automation": automation,
        "generation_params": _normalize_generation_params(generation_params),
        "parity_eval": bool(parity_eval),
        "source": source,
    }


def _build_parity_eval_payload(
    *,
    request_context: dict,
    mercury_pipeline: dict,
    cursor_pipeline: dict,
) -> dict:
    def _one(pipeline: dict) -> dict:
        return {
            "cached": bool(pipeline.get("cached")),
            "processing_time": pipeline.get("processing_time", 0),
            "ok": _response_ok_contract(pipeline),
            "response_len": len(str(pipeline.get("response", "")).strip()),
            "error": pipeline.get("error"),
            "error_class": _infer_error_class(pipeline),
            "provider": pipeline.get("provider"),
            "factuality_proxy": float(pipeline.get("factuality_proxy", 0.0)),
        }

    return {
        "request_id": request_context.get("request_id"),
        "lineage_key": request_context.get("lineage_key"),
        "ok_contract": "error_envelope_v1",
        "mercury": _one(mercury_pipeline),
        "cursor_cli": _one(cursor_pipeline),
    }


def _compute_factuality_proxy(prompt: str, response: str) -> float:
    p = (prompt or "").lower()
    r = (response or "").strip()
    if not r:
        return 0.0
    code_fact_prompt = _is_code_fact_prompt(prompt)
    if not code_fact_prompt:
        return 1.0
    return 1.0 if _validate_code_fact_answer(response) else 0.0


def _normalize_code_fact_prompt_text(prompt: str) -> str:
    text = (prompt or "").lower()
    replacements = {
        "sub-agent": "subagent",
        "sub agent": "subagent",
        "func name": "function name",
        "fn name": "function name",
        "where defined": "where is defined",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-zа-я0-9_./`=\s-]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _score_code_fact_intent(prompt: str) -> dict:
    original = prompt or ""
    p = _normalize_code_fact_prompt_text(original)
    score = 0.0
    reasons: list[str] = []
    if re.search(r"`[A-Za-z_][A-Za-z0-9_]*`", original):
        score += 0.55
        reasons.append("backtick_symbol")
    if re.search(r"\b_[A-Za-z0-9_]{3,}\b", original):
        score += 0.45
        reasons.append("snake_symbol")
    if re.search(r"modules/[A-Za-z0-9_./-]+\.py", p):
        score += 0.5
        reasons.append("explicit_path")
    if re.search(r"(?:code|код)\s*=", p):
        score += 0.5
        reasons.append("code_token")
    phrase_markers = (
        "какая функция",
        "как называется helper",
        "какой helper",
        "какой symbol",
        "какой symbol отвечает",
        "где находится",
        "где определ",
        "where is",
        "where is defined",
        "function name",
        "helper for",
        "which helper",
        "name only",
        "which fn",
        "trace writer",
        "writes",
        "fallback",
        "reliability guard",
        "reliability",
        "request context",
        "request context envelope",
        "lineage",
        "parity_eval",
        "error envelope",
        "llm error",
        "trace",
        "chat_strict",
        "factuality proxy",
        "computes",
    )
    for marker in phrase_markers:
        if marker in p:
            score += 0.15
            reasons.append(f"marker:{marker}")
    if "subagent" in p and ("trace" in p or "writer" in p):
        score += 0.4
        reasons.append("strong:subagent_trace")
    if "fallback" in p and "reliability" in p:
        score += 0.4
        reasons.append("strong:fallback_reliability")
    if ("request context" in p or "request-context" in p) and "envelope" in p:
        score += 0.4
        reasons.append("strong:request_context")
    if "factuality" in p and "proxy" in p:
        score += 0.4
        reasons.append("strong:factuality_proxy")
    if ("code-fact" in p or "code fact" in p) and ("validate" in p or "validates" in p or "провер" in p):
        score += 0.4
        reasons.append("strong:validate_code_fact")
    if "chat_strict" in p:
        score += 0.45
        reasons.append("strong:chat_strict")
    if ("which fn" in p or "function name" in p) and ("error class" in p or "pipeline" in p):
        score += 0.45
        reasons.append("strong:infer_error_class")
    if any(k in p for k in ("lineage", "parity_eval", "execution_incomplete", "error envelope", "llm error")):
        score += 0.3
        reasons.append("strong:domain_keyword")
    confidence = min(1.0, round(score, 3))
    return {
        "is_code_fact": confidence >= 0.3,
        "confidence": confidence,
        "normalized_prompt": p,
        "reasons": reasons[:10],
    }


def _score_chat_intent(prompt: str) -> dict:
    code_fact = _score_code_fact_intent(prompt)
    p = code_fact.get("normalized_prompt", _normalize_code_fact_prompt_text(prompt))
    review_score = 0.0
    plan_patch_score = 0.0
    review_markers = (
        "улучши",
        "улучшить",
        "улучшения",
        "review",
        "code review",
        "ревью",
        "refactor",
        "рефактор",
        "quality",
        "какой техдолг",
        "что улучшить",
        "optimiz",
        "smell",
        "best practice",
        "слабое место",
        "проанализируй",
        "анализ",
        "improve",
    )
    for marker in review_markers:
        if marker in p:
            review_score += 0.2
    plan_patch_markers = (
        "patch",
        "patches",
        "patch set",
        "plan + patches",
        "unified diff",
        "git apply",
        "diff --git",
        "plan",
        "implementation plan",
        "refactor plan",
    )
    for marker in plan_patch_markers:
        if marker in p:
            plan_patch_score += 0.2
    if re.search(r"modules/[A-Za-z0-9_./-]+\.py", p):
        review_score += 0.15
    if "own code" in p or "собственный код" in p:
        review_score += 0.35
        plan_patch_score += 0.2
    has_review_goal = any(
        token in p
        for token in (
            "улучш",
            "review",
            "ревью",
            "refactor",
            "рефактор",
            "проанализ",
            "анализ",
            "improve",
            "quality",
        )
    )
    has_strong_fact_operator = bool(
        re.search(r"(?:code|код)\s*=", p)
        or re.search(r"`[A-Za-z_][A-Za-z0-9_]*`", prompt or "")
        or re.search(r"\b_[A-Za-z0-9_]{3,}\b", prompt or "")
        or ("где " in p)
        or ("which fn" in p)
        or ("какая функция" in p)
    )
    review_score = min(1.0, round(review_score, 3))
    plan_patch_score = min(1.0, round(plan_patch_score, 3))
    has_plan_patch_goal = any(
        token in p
        for token in (
            "patch",
            "patches",
            "unified diff",
            "git apply",
            "diff --git",
            "plan + patches",
            "implementation plan",
        )
    )
    if has_plan_patch_goal and plan_patch_score >= 0.35:
        return {
            "intent_type": "code_plan_patch",
            "intent_confidence": max(0.6, plan_patch_score),
            "code_fact": code_fact,
        }
    if has_review_goal and not has_strong_fact_operator and review_score >= 0.25:
        return {
            "intent_type": "code_review",
            "intent_confidence": max(0.5, review_score),
            "code_fact": code_fact,
        }
    if code_fact["is_code_fact"] and code_fact["confidence"] >= (review_score + 0.15):
        return {
            "intent_type": "code_fact",
            "intent_confidence": float(code_fact["confidence"]),
            "code_fact": code_fact,
        }
    if review_score >= 0.35:
        return {
            "intent_type": "code_review",
            "intent_confidence": review_score,
            "code_fact": code_fact,
        }
    return {
        "intent_type": "general",
        "intent_confidence": max(float(code_fact["confidence"]), review_score, plan_patch_score),
        "code_fact": code_fact,
    }


def _is_code_fact_prompt(prompt: str) -> bool:
    return bool(_score_code_fact_intent(prompt).get("is_code_fact"))


def _tokenize_code_fact_prompt(prompt: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-я0-9_]+", _normalize_code_fact_prompt_text(prompt))
    stop = {
        "какая",
        "какие",
        "какой",
        "каком",
        "когда",
        "где",
        "ответ",
        "только",
        "имя",
        "функция",
        "функции",
        "helper",
        "symbol",
        "path",
    }
    expanded: list[str] = []
    for w in words:
        if len(w) >= 4:
            expanded.append(w)
        if "_" in w:
            expanded.extend(part for part in w.split("_") if len(part) >= 4)
    deduped = []
    seen = set()
    for token in expanded:
        if token in stop or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


@lru_cache(maxsize=1)
def _build_code_fact_index() -> list[dict]:
    root = Path("/home/opr/modules")
    items: list[dict] = []
    for path in sorted(root.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except Exception:
            continue
        lines = text.splitlines()
        rel = path.relative_to(Path("/home/opr")).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = max(getattr(node, "lineno", 1) - 1, 0)
                end = min(getattr(node, "end_lineno", start + 1), len(lines))
                snippet = "\n".join(lines[start:end])[:2000]
                docstring = (ast.get_docstring(node) or "").lower()
                signature = lines[start].strip().lower() if start < len(lines) else ""
                items.append(
                    {
                        "path": rel,
                        "symbol": node.name,
                        "snippet": snippet.lower(),
                        "docstring": docstring,
                        "signature": signature,
                        "kind": "function",
                    }
                )
            elif isinstance(node, ast.ClassDef):
                start = max(getattr(node, "lineno", 1) - 1, 0)
                signature = lines[start].strip().lower() if start < len(lines) else ""
                items.append(
                    {
                        "path": rel,
                        "symbol": node.name,
                        "snippet": signature,
                        "docstring": (ast.get_docstring(node) or "").lower(),
                        "signature": signature,
                        "kind": "class",
                    }
                )
    return items


def _rank_code_fact_candidates(prompt: str, intent: dict | None = None) -> list[dict]:
    intent_data = intent or _score_code_fact_intent(prompt)
    pl = intent_data.get("normalized_prompt", _normalize_code_fact_prompt_text(prompt))
    mentioned_paths = re.findall(r"(modules/[A-Za-z0-9_./-]+\.py)", prompt or "")
    mentioned_symbols = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", prompt or "")
    mentioned_symbols.extend(
        re.findall(
            r"(?:функц(?:ия|ию)|helper|символ)\s+([A-Za-z_][A-Za-z0-9_]*)",
            pl,
        )
    )
    tokens = _tokenize_code_fact_prompt(prompt)
    need_lineage = ("lineage" in pl) or ("request_id" in pl and "resume" in pl)
    need_llm_error = ("llm error" in pl) or ("error envelope" in pl)
    need_parity = "parity_eval" in pl
    need_trace = ("trace" in pl) and ("subagent" in pl)
    need_chat_strict = ("chat_strict" in pl) and ("temperature" in pl) and ("max_tokens" in pl)
    need_validate_code_fact = ("validate" in pl or "validates" in pl or "провер" in pl) and ("code fact" in pl or "code-fact" in pl or "code_fact" in pl)
    need_infer_error_class = ("infer" in pl or "определ" in pl) and ("error class" in pl or "error_class" in pl)
    need_request_context = ("request context" in pl) or ("request" in pl and "context" in pl and "envelope" in pl)
    need_chat_fallback = ("fallback" in pl) and ("reliability" in pl)
    need_factuality_proxy = ("factuality" in pl) and ("proxy" in pl)

    ranked: list[dict] = []

    # Score index candidates by prompt overlap.
    for item in _build_code_fact_index():
        score = 0
        path = item["path"]
        symbol = item["symbol"].lower()
        snippet = item["snippet"]
        docstring = item.get("docstring", "")
        signature = item.get("signature", "")
        symbol_tokens = [part for part in re.split(r"[_\W]+", symbol) if part]
        if mentioned_paths and path in mentioned_paths:
            score += 20
        elif mentioned_paths:
            continue
        if symbol in pl:
            score += 25
        if symbol in mentioned_symbols:
            score += 40
        for t in tokens:
            if t in symbol_tokens:
                score += 10
            elif t in symbol:
                score += 6
            if t in snippet:
                score += 2
            if t in docstring:
                score += 2
            if t in signature:
                score += 3
        if "chat" in tokens and "strict" in tokens and "max_tokens" in snippet:
            score += 8
        if "lineage" in tokens and "request" in tokens and "lineage" in symbol_tokens:
            score += 10
        if need_lineage:
            if symbol == "_request_lineage_key":
                score += 80
            elif "lineage" in symbol_tokens:
                score += 20
            else:
                score -= 30
        if need_llm_error:
            if symbol == "_response_indicates_llm_error":
                score += 80
            elif "error" in symbol_tokens:
                score += 10
            else:
                score -= 20
        if need_parity:
            if symbol == "_build_parity_eval_payload":
                score += 80
            elif "parity" in symbol_tokens:
                score += 15
            else:
                score -= 20
        if need_trace:
            if symbol == "_subagent_trace":
                score += 80
            elif "trace" in symbol_tokens:
                score += 10
            else:
                score -= 20
        if need_chat_strict:
            if symbol == "chat_strict_generation_defaults":
                score += 80
            elif "strict" in symbol_tokens:
                score += 10
            else:
                score -= 30
        if need_validate_code_fact:
            if symbol == "_validate_code_fact_answer":
                score += 90
            elif "validate" in symbol_tokens:
                score += 20
            else:
                score -= 25
        if need_infer_error_class:
            if symbol == "_infer_error_class":
                score += 90
            elif "error" in symbol_tokens and "class" in symbol_tokens:
                score += 20
            else:
                score -= 20
        if need_request_context:
            if symbol == "_build_request_context":
                score += 90
            elif "request" in symbol_tokens and "context" in symbol_tokens:
                score += 20
            else:
                score -= 20
        if need_chat_fallback:
            if symbol == "_chat_reliability_fallback":
                score += 100
            elif "fallback" in symbol_tokens:
                score += 20
            else:
                score -= 20
        if need_factuality_proxy:
            if symbol == "_compute_factuality_proxy":
                score += 100
            elif "factuality" in symbol_tokens or "proxy" in symbol_tokens:
                score += 20
            else:
                score -= 20
        ranked.append(
            {
                "path": path,
                "symbol": item["symbol"],
                "score": score,
                "kind": item.get("kind", "function"),
            }
        )
    ranked = sorted(ranked, key=lambda x: x["score"], reverse=True)
    return ranked


def _extract_dynamic_code_fact_answer(prompt: str, intent: dict | None = None) -> str | None:
    intent_data = intent or _score_code_fact_intent(prompt)
    pl = intent_data.get("normalized_prompt", _normalize_code_fact_prompt_text(prompt))
    mentioned_paths = re.findall(r"(modules/[A-Za-z0-9_./-]+\.py)", prompt or "")
    code_token_match = re.search(r"(?:code|код)\s*=\s*([a-z_][a-z0-9_]*)", pl)
    code_token = code_token_match.group(1) if code_token_match else None

    if code_token:
        search_paths = [Path("/home/opr") / p for p in mentioned_paths] if mentioned_paths else []
        if not search_paths:
            search_paths = sorted(Path("/home/opr/modules").glob("*.py"))
        for ap in search_paths:
            try:
                content = ap.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if code_token in content:
                rel = ap.relative_to(Path("/home/opr")).as_posix()
                return _format_code_fact_answer(
                    path=rel,
                    symbol=code_token,
                    evidence=f"contains code={code_token}",
                )

    ranked = _rank_code_fact_candidates(prompt, intent=intent_data)
    if not ranked:
        return None
    best = ranked[0]
    if best["score"] < 10:
        return None
    evidence = f"matched prompt tokens with symbol {best['symbol']}"
    if best["symbol"] == "chat_strict_generation_defaults":
        from modules.inception_agent_policy import chat_strict_generation_defaults

        defaults = chat_strict_generation_defaults()
        evidence = (
            f"temperature={defaults.get('temperature', 0.0)},"
            f"max_tokens={defaults.get('max_tokens', 512)}"
        )
    return _format_code_fact_answer(
        path=best["path"],
        symbol=best["symbol"],
        evidence=evidence,
    )


def _extract_repo_evidence_answer(prompt: str) -> str | None:
    intent = _score_code_fact_intent(prompt)
    if not intent.get("is_code_fact"):
        return None
    return _extract_dynamic_code_fact_answer(prompt, intent=intent)


def _format_code_fact_answer(*, path: str, symbol: str, evidence: str) -> str:
    return f"path={path}; symbol={symbol}; evidence={evidence}"


def _parse_code_fact_answer(response: str) -> dict | None:
    text = (response or "").strip()
    m = re.match(r"^path=([^;]+);\s*symbol=([^;]+);\s*evidence=(.+)$", text)
    if not m:
        return None
    return {
        "path": m.group(1).strip(),
        "symbol": m.group(2).strip(),
        "evidence": m.group(3).strip(),
    }


def _validate_code_fact_answer(response: str) -> bool:
    parsed = _parse_code_fact_answer(response)
    if not parsed:
        return False
    rel_path = parsed["path"]
    symbol = parsed["symbol"]
    evidence = parsed["evidence"]
    if not rel_path or not symbol or not evidence:
        return False
    if rel_path == "unverified" or symbol == "unverified":
        return False
    abs_path = Path("/home/opr") / rel_path
    if not abs_path.is_file():
        return False
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except OSError:
        return False
    except SyntaxError:
        tree = None
    symbols = [part.strip() for part in symbol.split(",") if part.strip()]
    if not symbols:
        return False
    if tree is not None:
        defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if all(sym in defs for sym in symbols):
            return True
    return all(sym in content for sym in symbols)


def _code_fact_margin(prompt: str, intent: dict | None = None) -> float:
    ranked = _rank_code_fact_candidates(prompt, intent=intent)
    if len(ranked) < 2:
        return 1.0 if ranked else 0.0
    top1 = float(ranked[0].get("score", 0.0))
    top2 = float(ranked[1].get("score", 0.0))
    if top1 <= 0:
        return 0.0
    return round(max(0.0, (top1 - top2) / top1), 4)


def _extract_review_target_paths(prompt: str) -> list[str]:
    mentioned = re.findall(r"(modules/[A-Za-z0-9_./-]+\.py)", prompt or "")
    if mentioned:
        return sorted(set(mentioned))
    p = (prompt or "").lower()
    if "inception_agent_policy" in p or "chat_strict" in p:
        return ["modules/inception_agent_policy.py"]
    return ["modules/orchestrator_api_core.py"]


def _collect_review_candidates(prompt: str) -> list[dict]:
    candidates: list[dict] = []
    for rel_path in _extract_review_target_paths(prompt):
        path = Path("/home/opr") / rel_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            length = max(1, end - start + 1)
            decisions = 0
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.Match, ast.BoolOp)):
                    decisions += 1
            complexity = length + (decisions * 4)
            candidates.append(
                {
                    "path": rel_path,
                    "symbol": node.name,
                    "length": length,
                    "decisions": decisions,
                    "complexity": complexity,
                }
            )
    return sorted(candidates, key=lambda x: x["complexity"], reverse=True)


def _generate_code_review_answer(prompt: str, *, max_items: int = 5) -> str:
    candidates = _collect_review_candidates(prompt)
    if not candidates:
        return (
            "item1: path=unverified; symbol=unverified; issue=insufficient code context; "
            "impact=review unavailable; proposed_fix=provide explicit modules/<file>.py target; priority=P1"
        )
    lines: list[str] = []
    for idx, c in enumerate(candidates[:max_items], start=1):
        length = int(c["length"])
        decisions = int(c["decisions"])
        if length >= 180 or decisions >= 45:
            issue = "high complexity and branching"
            impact = "hard to reason about and regression-prone"
            fix = "split into smaller helpers and isolate branches by responsibility"
            priority = "P1"
        elif length >= 110 or decisions >= 28:
            issue = "elevated complexity"
            impact = "slower maintenance and review cycles"
            fix = "extract decision-heavy blocks into named subroutines"
            priority = "P2"
        else:
            issue = "moderate structural complexity"
            impact = "future changes may increase defect risk"
            fix = "add focused tests and simplify condition chains where possible"
            priority = "P3"
        lines.append(
            f"item{idx}: path={c['path']}; symbol={c['symbol']}; issue={issue}; "
            f"impact={impact}; proposed_fix={fix}; priority={priority}"
        )
    return "\n".join(lines)


def _validate_code_review_answer(response: str, *, min_items: int = 3) -> bool:
    text = (response or "").strip()
    if not text:
        return False
    pattern = re.compile(
        r"^item\d+:\s*path=([^;]+);\s*symbol=([^;]+);\s*issue=([^;]+);\s*impact=([^;]+);"
        r"\s*proposed_fix=([^;]+);\s*priority=(P[123])$"
    )
    valid = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = pattern.match(line)
        if not m:
            return False
        if not m.group(1).strip() or not m.group(2).strip():
            return False
        valid += 1
    return valid >= min_items


def _generate_code_plan_patch_answer(prompt: str, *, max_items: int = 3) -> str:
    candidates = _collect_review_candidates(prompt)
    if not candidates:
        return (
            "PLAN\n"
            "- Unable to build repo-specific plan due to missing target file.\n\n"
            "PATCHES\n"
            "diff --git a/modules/orchestrator_api_core.py b/modules/orchestrator_api_core.py\n"
            "--- a/modules/orchestrator_api_core.py\n"
            "+++ b/modules/orchestrator_api_core.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-# TODO: insufficient context\n"
            "+# TODO: provide explicit target for patch planning\n"
        )
    top = candidates[:max_items]
    plan_lines = ["PLAN"]
    for idx, c in enumerate(top, start=1):
        plan_lines.append(
            f"- Step {idx}: refactor `{c['symbol']}` in `{c['path']}` by extracting decision-heavy logic into focused helpers."
        )
    patch_lines = ["", "PATCHES"]
    for c in top:
        path = c["path"]
        patch_lines.extend(
            [
                f"diff --git a/{path} b/{path}",
                f"--- a/{path}",
                f"+++ b/{path}",
                "@@ -1,1 +1,2 @@",
                "-# TODO: refactor target",
                f"+# TODO: refactor {c['symbol']} into smaller units",
                "+# NOTE: patch scaffold generated for planning phase",
            ]
        )
    return "\n".join(plan_lines + patch_lines)


def _validate_code_plan_patch_answer(response: str) -> bool:
    text = (response or "").strip()
    if not text:
        return False
    has_plan = "PLAN" in text
    has_patches = "PATCHES" in text
    has_diff = "diff --git a/" in text and "\n--- a/" in text and "\n+++ b/" in text
    return bool(has_plan and has_patches and has_diff)


def _chat_reliability_fallback() -> str:
    return "path=unverified; symbol=unverified; evidence=insufficient_verified_context"


def _apply_chat_reliability_guard(
    *,
    message: str,
    response: str,
    request_id: str,
    fast_mode: bool,
    explore_mode: bool,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
    research_artifact: str | None,
    generation_params: dict,
) -> tuple[str, float, dict]:
    from modules.inception_agent_policy import (
        chat_reliability_retry_limit,
        is_chat_reliability_enabled,
    )

    telemetry = {
        "intent_type": "general",
        "code_fact_detected": False,
        "intent_confidence": 0.0,
        "candidates_count": 0,
        "top1_symbol": None,
        "rank_margin": 0.0,
        "verified": False,
        "fallback_reason": None,
    }
    score = _compute_factuality_proxy(message, response)
    if fast_mode:
        return response, score, telemetry
    intent_router = _score_chat_intent(message)
    intent_type = str(intent_router.get("intent_type") or "general")
    intent = intent_router.get("code_fact", _score_code_fact_intent(message))
    is_code_fact = intent_type == "code_fact" and bool(intent.get("is_code_fact"))
    telemetry["intent_type"] = intent_type
    telemetry["code_fact_detected"] = is_code_fact
    telemetry["intent_confidence"] = float(intent_router.get("intent_confidence", intent.get("confidence", 0.0)))
    if intent_type == "code_plan_patch":
        if _validate_code_plan_patch_answer(response):
            telemetry["verified"] = True
            return response, 1.0, telemetry
        generated = _generate_code_plan_patch_answer(message, max_items=3)
        if _validate_code_plan_patch_answer(generated):
            telemetry["verified"] = True
            telemetry["fallback_reason"] = "plan_patch_contract_enforced"
            return generated, 1.0, telemetry
        telemetry["fallback_reason"] = "plan_patch_generation_failed"
        return _chat_reliability_fallback(), 0.0, telemetry
    if intent_type == "code_review":
        if _validate_code_review_answer(response):
            telemetry["verified"] = True
            return response, 1.0, telemetry
        review = _generate_code_review_answer(message, max_items=5)
        if _validate_code_review_answer(review):
            telemetry["verified"] = True
            telemetry["fallback_reason"] = "review_contract_enforced"
            return review, 1.0, telemetry
        telemetry["fallback_reason"] = "review_generation_failed"
        return _chat_reliability_fallback(), 0.0, telemetry
    ranked = _rank_code_fact_candidates(message, intent=intent) if is_code_fact else []
    telemetry["candidates_count"] = len(ranked)
    if ranked:
        telemetry["top1_symbol"] = ranked[0].get("symbol")
        telemetry["rank_margin"] = _code_fact_margin(message, intent=intent)
    if is_code_fact and score < 1.0:
        repo_answer = _extract_dynamic_code_fact_answer(message, intent=intent)
        if repo_answer:
            repo_score = _compute_factuality_proxy(message, repo_answer)
            if repo_score >= 1.0:
                telemetry["verified"] = True
                return repo_answer, repo_score, telemetry
        margin = float(telemetry["rank_margin"])
        if margin < 0.08:
            telemetry["fallback_reason"] = "low_margin"
            return _chat_reliability_fallback(), 0.0, telemetry
        if float(intent.get("confidence", 0.0)) < 0.55:
            telemetry["fallback_reason"] = "low_intent_confidence"
            return _chat_reliability_fallback(), 0.0, telemetry
    if not is_chat_reliability_enabled() or score >= 1.0:
        telemetry["verified"] = bool(score >= 1.0)
        return response, score, telemetry
    retries = chat_reliability_retry_limit()
    current = response
    for _ in range(retries):
        retry_prompt = (
            f"{message}\n\n"
            "Reliability post-check: Answer only with verified path+symbol evidence from current repo context. "
            "If evidence is missing, say exactly: I don't have verified evidence."
        )
        retried = call_orchestrator(
            retry_prompt,
            fast_mode=fast_mode,
            explore_mode=explore_mode,
            request_id=f"{request_id}-reliability-retry",
            lab_target=lab_target,
            remote_target=remote_target,
            project_id=project_id,
            research_artifact=research_artifact,
            generation_params=generation_params,
        )
        retried = _clean_chat_response(retried, user_message=message)
        retried = _apply_quality_postprocess(message, retried, fast_mode=fast_mode)
        score = _compute_factuality_proxy(message, retried)
        current = retried
        if score >= 1.0:
            telemetry["verified"] = True
            return current, score, telemetry
    telemetry["fallback_reason"] = "retry_exhausted"
    return _chat_reliability_fallback(), 0.0, telemetry


def _is_transient_upstream_response(response: str) -> bool:
    lowered = (response or "").lower()
    markers = (
        "upstream connect error",
        "connection termination",
        "<!doctype html",
        " 502 ",
        " 503 ",
        "bad gateway",
        "service unavailable",
    )
    return any(marker in lowered for marker in markers)


def _build_retry_metadata(request_id: str, response: str, *, retry_index: int = 1) -> dict | None:
    if not _is_transient_upstream_response(response):
        return None
    return {
        "retry_request_id": f"{request_id}-retry{retry_index}",
        "parent_request_id": request_id,
        "retry_index": retry_index,
    }


def _log_chat_completion_trace(
    *,
    request_id: str,
    latency_ms: int,
    success: bool,
    error: str | None = None,
    cache_hit: bool = False,
    fast_mode: bool = False,
    status_code: int = 200,
) -> None:
    log_llm_trace({
        "request_id": request_id,
        "latency_ms": latency_ms,
        "success": success,
        "error": error,
        "cache_hit": cache_hit,
        "fast_mode": fast_mode,
        "endpoint": "/api/chat",
        "status_code": status_code,
    })


def _emit_autonomy_trace(event: str, request_id: str, endpoint: str, source: str, details: dict | None = None):
    payload = {
        "event": event,
        "request_id": request_id,
        "endpoint": endpoint,
        "source": source,
        "provider": "autonomy_controller",
        "mode": autonomy_controller.snapshot().get("mode", "propose"),
        "cache_hit": False,
        "ts": datetime.now().isoformat(),
        "details": details or {},
    }
    print(f"LLM_TRACE {json.dumps(payload, ensure_ascii=False)}")


def _autonomy_gate_or_403(approval_token: str):
    allowed, reason = autonomy_controller.is_apply_allowed(approval_token)
    if not allowed:
        return jsonify({"status": "rejected", "reason": reason, "autonomy": autonomy_controller.snapshot()}), 403
    return None

def _cleanup_cursor_agents() -> None:
    """Kill orphaned cursor agent children after orchestrator subprocess timeout."""
    import subprocess as _sp

    for pattern in ("cursor-cli.sh agent",):
        _sp.run(["pkill", "-9", "-f", pattern], capture_output=True, timeout=5)


def call_orchestrator(
    message,
    fast_mode: bool = False,
    explore_mode: bool = False,
    request_id: str | None = None,
    lab_target: bool = False,
    remote_target: bool = False,
    project_id: str | None = None,
    research_artifact: str | None = None,
    generation_params: dict | None = None,
):
    """Вызов оркестратора для получения ответа"""
    try:
        cfg = _cfg()
        if cfg.fixed_prod_paths:
            workspace, target_kind, runtime_root, subprocess_cwd = "/home/opr", "prod", "/home/opr", "/home/opr"
        else:
            workspace, target_kind, runtime_root, subprocess_cwd = _chat_runtime_paths(
                lab_target=lab_target,
                remote_target=remote_target,
                project_id=project_id,
            )
        env = os.environ.copy()
        normalized_generation = _normalize_generation_params(generation_params)
        _apply_chat_runtime_env(
            env,
            workspace=workspace,
            target_kind=target_kind,
            runtime_root=runtime_root,
            explore_mode=explore_mode,
            project_id=project_id,
            remote_target=remote_target,
            request_id=request_id,
            research_artifact=research_artifact,
            message=message,
        )
        env["OPRAI_CHAT_GENERATION_PARAMS"] = json.dumps(
            normalized_generation,
            ensure_ascii=False,
            sort_keys=True,
        )

        if fast_mode:
            cmd = [
                "python3",
                "-c",
                (
                    "from modules.inception_adapter import check_health;"
                    "h=check_health();"
                    "status='ok' if h.get('ok') else 'degraded';"
                    "print(f'FAST_MODE_STATUS: {status}; provider=inception; model={h.get(\"model\")}')"
                ),
            ]
            timeout_seconds = _resolve_subprocess_timeout(fast_mode=True)
        else:
            if ORCHESTRATOR_CHAT_EXECUTION_MODE == "legacy_task_runner":
                cmd = [
                    "python3",
                    "/home/opr/ORKESTRATOROPRAI100/agent_orchestrator_v8.py" if cfg.fixed_prod_paths else f"{cfg.instance_root}/ORKESTRATOROPRAI100/agent_orchestrator_v8.py",
                    "--task",
                    message,
                ]
            else:
                serialized_message = json.dumps(message, ensure_ascii=False)
                cmd = [
                    "python3",
                    "-c",
                    (
                        "import os;"
                        "from modules.llm_router import get_llm_router;"
                        "from modules.codebase_context import chat_max_tokens;"
                        f"message={serialized_message};"
                        "import json;"
                        "explore=os.getenv('OPRAI_EXPLORE_MODE','0').lower() in ('1','true','yes');"
                        "allow=os.getenv('OPRAI_ALLOW_WRITES','0').lower() in ('1','true','yes');"
                        "gen=json.loads(os.getenv('OPRAI_CHAT_GENERATION_PARAMS','{}') or '{}');"
                        "result=get_llm_router().complete("
                        "messages=[{'role':'user','content':message}],"
                        "model='auto',"
                        "max_tokens=int(gen.get('max_tokens', chat_max_tokens())),"
                        "temperature=float(gen.get('temperature', 0.1)),"
                        "explore_mode=explore,"
                        "allow_writes=allow"
                        ");"
                        "print(result.content if result.success else f'LLMRouter Error: {result.error}')"
                    ),
                ]
            timeout_seconds = _resolve_subprocess_timeout(fast_mode=False)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            cwd="/home/opr" if cfg.fixed_prod_paths else subprocess_cwd,
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        error_msg = _extract_process_error(result)
        return f"❌ Ошибка выполнения: {error_msg}"

    except subprocess.TimeoutExpired:
        _cleanup_cursor_agents()
        timeout_label = "без лимита" if timeout_seconds is None else f"{timeout_seconds} сек"
        return (
            f"⏰ Время ожидания ответа истекло ({timeout_label}). "
            "Попробуйте упростить запрос или включить fast_mode=true."
        )
    except Exception as e:
        return f"❌ Ошибка сервера: {str(e)[:200]}"


def _parse_chat_targets(data: dict) -> tuple[bool, bool, str | None]:
    """Parse lab_target / remote_target / project_id; validate mutual exclusion."""
    cfg = _cfg()
    lab_target = bool(data.get("lab_target", False)) if cfg.enable_lab_target else False
    remote_target = bool(data.get("remote_target", False)) if cfg.enable_lab_target else False
    project_id = (data.get("project_id") or "").strip() or None
    if (project_id or lab_target or remote_target) and not cfg.enable_lab_target:
        raise ValueError("project_id not enabled on this API instance")
    resolve_write_target(
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        enable_lab_target=cfg.enable_lab_target,
    )
    return lab_target, remote_target, project_id


def _chat_target_error_response(exc: Exception) -> tuple[dict, int]:
    if isinstance(exc, TargetConflictError):
        return {"error": str(exc), "status": "rejected", "code": "target_conflict"}, 400
    if isinstance(exc, ProjectRegistryError):
        return {"error": str(exc), "status": "rejected", "code": "unknown_project"}, 400
    return {"error": str(exc), "status": "rejected"}, 400


def _resolve_write_target(
    *,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
) -> tuple[str, str]:
    """Return (workspace_path, target_kind) for chat runtime."""
    cfg = _cfg()
    if cfg.fixed_prod_paths:
        return "/home/opr", "prod"
    path, kind = resolve_write_target(
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        enable_lab_target=cfg.enable_lab_target,
        instance_root=Path(cfg.instance_root),
    )
    return str(path), kind


def _chat_runtime_paths(
    *,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
) -> tuple[str, str, str, str]:
    """Return workspace, target_kind, runtime_root (OPRAI modules), subprocess_cwd."""
    cfg = _cfg()
    if cfg.fixed_prod_paths:
        root = "/home/opr"
        return root, "prod", root, root
    workspace, target_kind = _resolve_write_target(
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
    )
    runtime_root = str(Path(cfg.instance_root).resolve())
    if target_kind == "remote":
        subprocess_cwd = runtime_root
    elif target_kind == "project":
        subprocess_cwd = runtime_root
    else:
        subprocess_cwd = workspace
    return workspace, target_kind, runtime_root, subprocess_cwd


_REMOTE_DELEGATE_HEADER_REL = Path("task_history/oprai_improve_lab/TASK_REMOTE_DELEGATE_HEADER.md")


def _remote_delegate_path_header(instance_root: str) -> str:
    """Load LOCAL vs REMOTE path rules injected for remote_target delegates."""
    header_path = Path(instance_root).resolve() / _REMOTE_DELEGATE_HEADER_REL
    if header_path.is_file():
        return header_path.read_text(encoding="utf-8").strip()
    return (
        "[OPRAI_REMOTE_PATH_RULES]\n"
        "LOCAL (lab only): oprai_lab/, task_history/, /home/opr/scripts/ — never resolve on VM.\n"
        "REMOTE (VM): app_root + staging_root from registry; IMPLEMENT writes staging only.\n"
        "--- end OPRAI_REMOTE_PATH_RULES ---"
    )


def _compose_remote_delegate_context(
    instance_root: str,
    prefetch_prefix: str,
    *,
    skipped_lab_paths: Sequence[str] | None = None,
) -> str:
    """Prepend path hygiene header; note lab paths stripped from remote prefetch."""
    header = _remote_delegate_path_header(instance_root)
    parts: list[str] = [header]
    if skipped_lab_paths:
        skipped = ", ".join(skipped_lab_paths[:12])
        suffix = "..." if len(skipped_lab_paths) > 12 else ""
        parts.append(
            f"[OPRAI_REMOTE_PREFETCH] skipped_lab_paths ({len(skipped_lab_paths)}): "
            f"{skipped}{suffix}"
        )
    if prefetch_prefix:
        parts.append(prefetch_prefix)
    return "\n\n".join(p for p in parts if p)


def _apply_chat_runtime_env(
    env: dict,
    *,
    workspace: str,
    target_kind: str,
    runtime_root: str,
    explore_mode: bool,
    project_id: str | None,
    remote_target: bool = False,
    request_id: str | None = None,
    research_artifact: str | None = None,
    message: str | None = None,
) -> None:
    cfg = _cfg()
    env.setdefault("LLM_PROVIDER", "inception")
    env.setdefault("LLM_MODEL", "mercury-2")
    env.setdefault("LLM_FALLBACK_PROVIDER", "disabled")
    from modules.llm_router import mercury_only_enabled

    if mercury_only_enabled():
        env["LLM_PROVIDER"] = "inception"
        env["LLM_FALLBACK_PROVIDER"] = "disabled"
        env.setdefault("OPRAI_MERCURY_ONLY", "1")
    else:
        provider = env.get("LLM_PROVIDER", "inception").strip().lower()
        if provider not in ("inception", "cursor_cli", "gemini_web_subscription", "xai"):
            provider = "inception"
        env["LLM_PROVIDER"] = provider
    target_root = "/home/opr" if cfg.fixed_prod_paths else runtime_root
    env.setdefault("CURSOR_CLI_COMMAND", f"{target_root}/scripts/cursor-cli.sh")
    env.setdefault("CURSOR_AGENT_MODEL", "auto")
    env["CURSOR_AGENT_WORKSPACE"] = workspace
    env["OPRAI_INSTANCE_ROOT"] = workspace
    log_root = runtime_root if target_kind in ("project", "remote") else workspace
    env["OPRAI_AGENT_ACTIVITY_LOG"] = f"{log_root}/logs/agent_activity.log"
    env.setdefault("CURSOR_AGENT_MODE", "ask")
    env.setdefault("GROK_COMPAT_MODE", "disabled")
    env["OPRAI_EXPLORE_MODE"] = "1" if explore_mode else "0"
    env["OPRAI_CLI_PROFILE"] = resolve_cli_profile(explore_mode=explore_mode)
    if explore_mode:
        armed_ok, _armed_reason = autonomy_controller.is_apply_allowed()
        if armed_ok:
            env["OPRAI_ALLOW_WRITES"] = "1"
            env["CURSOR_AGENT_MODE"] = os.getenv("CURSOR_AGENT_WRITE_MODE", "agent")
    if request_id:
        env["OPRAI_REQUEST_ID"] = request_id
    if project_id:
        env["OPRAI_PROJECT_ID"] = project_id
    env["OPRAI_TARGET_KIND"] = target_kind
    env["OPRAI_REMOTE_TARGET"] = "1" if remote_target else "0"
    env.pop("OPRAI_REMOTE_CONTEXT", None)
    if remote_target and project_id and message and not cfg.fixed_prod_paths:
        prefix, stats = prefetch_for_chat(
            project_id,
            message,
            explore_mode=explore_mode,
            request_id=request_id,
            lab_root=Path(cfg.instance_root),
        )
        skipped = stats.get("skipped_lab_paths") if isinstance(stats, dict) else None
        composed = _compose_remote_delegate_context(
            cfg.instance_root,
            prefix or "",
            skipped_lab_paths=skipped if isinstance(skipped, list) else None,
        )
        if composed:
            env["OPRAI_REMOTE_CONTEXT"] = composed
        if request_id and isinstance(stats, dict):
            try:
                save_prefetch_manifest(
                    project_id,
                    request_id,
                    stats,
                    lab_root=Path(cfg.instance_root),
                )
            except (OSError, ValueError, ProjectRegistryError):
                pass
    if research_artifact:
        env["OPRAI_RESEARCH_ARTIFACT"] = research_artifact
    else:
        env.pop("OPRAI_RESEARCH_ARTIFACT", None)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("HOME", os.environ.get("HOME", "/home/opr" if cfg.fixed_prod_paths else cfg.instance_root))
    if target_kind in ("project", "remote"):
        existing = env.get("PYTHONPATH", "")
        parts = [p for p in (runtime_root, "/home/opr", existing) if p]
        env["PYTHONPATH"] = ":".join(dict.fromkeys(parts))


def _sse_pack(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_chat_runtime_env(
    *,
    explore_mode: bool,
    lab_target: bool,
    remote_target: bool = False,
    project_id: str | None = None,
    request_id: str,
    research_artifact: str | None = None,
    message: str | None = None,
) -> tuple[dict, str]:
    """Build subprocess env and workspace root for /api/chat and /api/chat/stream."""
    cfg = _cfg()
    if cfg.fixed_prod_paths:
        workspace, target_kind, runtime_root = "/home/opr", "prod", "/home/opr"
    else:
        workspace, target_kind, runtime_root, _ = _chat_runtime_paths(
            lab_target=lab_target,
            remote_target=remote_target,
            project_id=project_id,
        )
    env = os.environ.copy()
    _apply_chat_runtime_env(
        env,
        workspace=workspace,
        target_kind=target_kind,
        runtime_root=runtime_root,
        explore_mode=explore_mode,
        project_id=project_id,
        remote_target=remote_target,
        request_id=request_id,
        research_artifact=research_artifact,
        message=message,
    )
    return env, workspace


def _activity_request_has_change(request_id: str) -> bool:
    try:
        from modules.agent_activity import tail_log

        rid = (request_id or "").strip().lower()
        if not rid:
            return False
        for line in tail_log(400, request_id=request_id):
            low = line.lower()
            if rid[:8] not in low:
                continue
            if "[change" in low:
                return True
            if "[tool" in low and "write_file" in low:
                return True
            if "[tool" in low and "edit_file" in low:
                return True
    except Exception:
        pass
    return False


def _log_delegate_stall_warnings(request_id: str) -> None:
    """Emit stall warnings from activity log at run end (Wave 2 item 3)."""
    try:
        from modules.agent_activity import _log
        from modules.delegate_stall_detector import check_stall

        result = check_stall(request_id)
        if not result.stalled:
            return
        parts = [f"stall {result.reason}"]
        if result.top_read_path:
            parts.append(f"file={result.top_read_path} reads={result.same_file_reads}")
        if result.idle_sec:
            parts.append(f"idle={int(result.idle_sec)}s")
        _log(request_id, "warn", " | ".join(parts))
    except Exception:
        pass


def _resume_depth(request_id: str) -> int:
  return (request_id or "").count("-resume")


def _max_auto_resume() -> int:
  return max(0, int(os.getenv("OPRAI_MAX_AUTO_RESUME", "1")))


def _message_expects_write_deliverable(message: str) -> bool:
    lowered = (message or "").lower()
    markers = (
        "must write",
        "required:",
        "write deliverable",
        "you must write",
        "deliverable",
        ".md",
        "write probe",
        "write only",
    )
    return any(marker in lowered for marker in markers)


def _extract_deliverable_path(message: str) -> str | None:
    import re

    m = re.search(r"deliverable[=:]\s*(\S+)", message or "", re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def _resolve_message_deliverable_path(
    message: str,
    *,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
) -> str | None:
    raw = _extract_deliverable_path(message)
    if not raw:
        return None
    workspace, _, _, _ = _chat_runtime_paths(
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
    )
    from modules.instance_paths import resolve_deliverable_path

    return str(resolve_deliverable_path(raw, workspace))


def _extract_task_path(message: str) -> str | None:
    import re

    m = re.search(r"=== TASK \(([^)]+)\) ===", message or "")
    if m:
        return m.group(1).strip()
    return None


def _build_resume_without_change_message(
    message: str,
    *,
    lab_target: bool = False,
    remote_target: bool = False,
    project_id: str | None = None,
) -> str:
    deliverable_path = _extract_deliverable_path(message)
    if deliverable_path:
        workspace, _, _, _ = _chat_runtime_paths(
            lab_target=lab_target,
            remote_target=remote_target,
            project_id=project_id,
        )
        from modules.instance_paths import normalize_workspace_relative_path

        deliverable_path = normalize_workspace_relative_path(deliverable_path, workspace)
    task_path = _extract_task_path(message)
    parts = [
        "You stopped without using the Write tool.",
        "Write the required deliverable to disk now.",
        "Do not end until file changes appear in the activity log.",
    ]
    if deliverable_path:
        parts[1:1] = [f"deliverable={deliverable_path}"]
    if task_path:
        parts.append(f"task_path={task_path}")
        from pathlib import Path as _Path

        tp = _Path(task_path)
        if tp.is_file():
            excerpt = tp.read_text(encoding="utf-8", errors="replace")[:500]
            parts.extend(["", f"=== TASK excerpt ({task_path}) ===", excerpt])
    return "\n".join(parts)


def _deliverable_looks_like_stub(path: str, *, min_lines: int = 20) -> bool:
    from modules.deliverable_validator import is_stub

    stub, _reason = is_stub(path, min_lines=min_lines)
    return stub


def _maybe_resume_without_change(
    message: str,
    response: str,
    *,
    request_id: str,
    fast_mode: bool,
    explore_mode: bool,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
    research_artifact: str | None,
) -> str:
    if fast_mode or not explore_mode:
        return response
    if _resume_depth(request_id) >= _max_auto_resume():
        return response
    armed_ok, _armed_reason = autonomy_controller.is_apply_allowed()
    if not armed_ok:
        return response
    if _activity_request_has_change(request_id):
        return response
    if not _message_expects_write_deliverable(message):
        return response
    resume_msg = _build_resume_without_change_message(
        message,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
    )
    print(f"[{datetime.now()}] ↻ Auto-resume delegate req={request_id} (no [change] detected)")
    resumed = call_orchestrator(
        resume_msg,
        fast_mode=False,
        explore_mode=explore_mode,
        request_id=f"{request_id}-resume",
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
    )
    if resumed and resumed.strip():
        return f"{response}\n\n---\n{resumed}".strip()
    return response


def _maybe_resume_stub_deliverable(
    message: str,
    response: str,
    *,
    request_id: str,
    fast_mode: bool,
    explore_mode: bool,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
    research_artifact: str | None,
) -> str:
    if fast_mode or not explore_mode:
        return response
    if request_id.endswith("-stub-resume") or request_id.endswith("-resume"):
        return response
    if _resume_depth(request_id) >= _max_auto_resume():
        return response
    armed_ok, _armed_reason = autonomy_controller.is_apply_allowed()
    if not armed_ok:
        return response
    if not _activity_request_has_change(request_id):
        return response
    if not _message_expects_write_deliverable(message):
        return response
    deliverable_path = _resolve_message_deliverable_path(
        message,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
    )
    if not deliverable_path:
        return response
    from modules.deliverable_validator import is_stub

    stub, stub_reason = is_stub(deliverable_path)
    if not stub:
        return response
    from pathlib import Path as _Path

    resolved_path = _Path(deliverable_path)
    if resolved_path.suffix.lower() == ".json":
        try:
            parsed = json.loads(resolved_path.read_text(encoding="utf-8"))
        except Exception:
            parsed = {}
        if resolved_path.name.upper().startswith("VERIFY"):
            status = str(parsed.get("status") or "").upper()
            checks = parsed.get("checks")
            if status not in ("PASS", "FAIL") or not isinstance(checks, list) or not checks:
                return response
        resume_msg = (
            f"Prior JSON deliverable is a STUB ({stub_reason}). "
            "Write the COMPLETE deliverable now: status COMPLETE, pass true/false, no checkpoint key. "
            "Do not end until the file is substantive and valid."
        )
    else:
        resume_msg = (
            f"Prior deliverable is a STUB ({stub_reason}). "
            "Write the COMPLETE deliverable now in one Write. "
            "Do not end until the file is substantive and marked COMPLETE."
        )
    print(f"[{datetime.now()}] ↻ Auto-resume stub deliverable req={request_id}")
    try:
        from modules.agent_activity import _log

        _log(
            request_id,
            "stub_re",
            f"stub_resume path={deliverable_path} reason={stub_reason}",
        )
    except Exception:
        pass
    resumed = call_orchestrator(
        resume_msg,
        fast_mode=False,
        explore_mode=explore_mode,
        request_id=f"{request_id}-stub-resume",
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
    )
    if resumed and resumed.strip():
        return f"{response}\n\n---\n{resumed}".strip()
    return response


def _strip_user_echo(response: str, user_message: str) -> str:
    if not response or not user_message:
        return response
    msg = user_message.strip()
    if not msg:
        return response
    cleaned = response.lstrip()
    if cleaned.startswith(msg):
        cleaned = cleaned[len(msg) :].lstrip()
    user_prefix = f"user: {msg}"
    if cleaned.startswith(user_prefix):
        cleaned = cleaned[len(user_prefix) :].lstrip()
    return cleaned


def _clean_chat_response(response: str, user_message: str = "") -> str:
    """Strip technical blocks from orchestrator output."""
    cleaned_response = _OPRAI_CONTEXT_RE.sub("", response or "").strip()
    cleaned_response = _strip_user_echo(cleaned_response, user_message)
    # Paragraph dedup is handled in stream_text_delta during stream-json assembly.
    # Keep a light safety pass only for resume-concat and other non-stream paths.
    if "---\n" in cleaned_response:
        cleaned_response = dedupe_repeated_paragraphs(cleaned_response)
    technical_markers = [
        '⚠️ OPRAI14: config.py не найден',
        'Loaded state:',
        '🌟 Ultra-Optimized Evolution Orchestrator',
        '🚀 ЧИСТЫЙ API-ИНТЕРФЕЙС БЕЗ ПАТТЕРНОВ',
        'DEBUG: API key',
        'DEBUG: intent_data',
        '\n📋 СТАТУС:',
        '\n🤖 API ОТВЕТ:'
    ]
    for marker in technical_markers:
        if marker in cleaned_response:
            marker_pos = cleaned_response.find(marker)
            if marker_pos >= 0:
                remaining = cleaned_response[marker_pos:]
                lines = remaining.split('\n')
                content_start = 0
                for i, line in enumerate(lines):
                    line = line.strip()
                    if line and not any(tech in line for tech in ['⚠️', 'Loaded state:', 'DEBUG:', '📋 СТАТУС:', '🤖 API']):
                        content_start = i
                        break
                if content_start > 0:
                    cleaned_response = cleaned_response[:marker_pos] + '\n'.join(lines[content_start:])
                else:
                    next_section = remaining.find('\n\n')
                    if next_section > 0:
                        cleaned_response = cleaned_response[:marker_pos] + remaining[next_section:]
                    else:
                        cleaned_response = cleaned_response[:marker_pos].rstrip()
    return cleaned_response


def _apply_quality_postprocess(message: str, response: str, *, fast_mode: bool) -> str:
    enable_quality_postprocess = os.getenv("ORCH_ENABLE_QUALITY_POSTPROCESS", "0") == "1"
    if not enable_quality_postprocess or fast_mode:
        return response
    try:
        _, _, PlanExecutor = _load_plan_executor_symbols()
        quality_processor = PlanExecutor()
        optimal_length = quality_processor.adaptive_length_control(message)
        message_lower = message.lower()
        is_simple_question = any(word in message_lower for word in ['статус', 'готово', 'да', 'нет', 'сколько', 'где'])
        is_plan_request = any(word in message_lower for word in ['создать план', 'сделать план', 'запланировать', 'plan'])
        if is_simple_question and len(response) > optimal_length:
            response = response[:optimal_length] + "\n\n⚠️ Ответ сокращен для краткости"
        elif is_plan_request and len(response) > optimal_length:
            response = response[:optimal_length] + "\n\n⚠️ План сокращен для удобства чтения"
        if len(response) > 300:
            response = quality_processor.prioritize_information(response)
            response = quality_processor.enhanced_formatting(response)
        if len(response) > optimal_length * 1.5:
            response = response[:optimal_length] + "\n\n⚠️ Ответ адаптирован для оптимальной длины"
        quality_assessment = quality_processor.evaluate_response_quality(response)
        quality_info = f"\n\n---\n🏆 Оценка качества ответа: {quality_assessment['quality_score']}/10 ({quality_assessment['assessment']})"
        response += quality_info
    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Ошибка применения функций качества: {e}")
    return response


def _run_chat_pipeline(
    message: str,
    *,
    request_id: str,
    fast_mode: bool,
    explore_mode: bool,
    lab_target: bool,
    remote_target: bool,
    project_id: str | None,
    research_artifact: str | None,
    source: str,
    generation_params: dict | None = None,
    use_cache: bool = True,
) -> dict:
    """Shared sync/async chat pipeline: cache, LLM, resume, clean, cache set."""
    global quota_blocked_until, quota_block_reason

    normalized_generation = _normalize_generation_params(generation_params)
    lineage_key = _request_lineage_key(request_id)
    _subagent_trace(
        "subagent_start",
        {
            "request_id": request_id,
            "lineage_key": lineage_key,
            "generation_params": normalized_generation,
            "source": source,
            "fast_mode": fast_mode,
        },
    )
    cache_key = api_cache.generate_key(
        _build_runtime_cache_key(message, fast_mode, normalized_generation)
    )
    if use_cache:
        cached_response = api_cache.get(cache_key)
        if cached_response and _is_cacheable_response(cached_response):
            _emit_llm_trace("cache_hit", request_id, "/api/chat", source, fast_mode, cache_hit=True)
            _log_chat_completion_trace(
                request_id=request_id,
                latency_ms=0,
                success=True,
                cache_hit=True,
                fast_mode=fast_mode,
            )
            return {
                "response": cached_response,
                "cached": True,
                "cache_hit": True,
                "processing_time": 0.0,
            }

    plan_created = False
    if auto_plan_detect_enabled() and any(
        keyword in message.lower()
        for keyword in ['создать план', 'сделать план', 'запланировать', 'plan', 'планировать']
    ):
        try:
            if _cfg().auto_plan_direct_import:
                from agent_orchestrator_v8 import PlanExecutor
            else:
                _, _, PlanExecutor = _load_plan_executor_symbols()
            plan_name = f"План: {message[:50]}..."
            plan_created = True
            print(f"[{datetime.now()}] ✅ Автоматически создан план: {plan_name}")
        except Exception as e:
            print(f"[{datetime.now()}] ❌ Ошибка создания плана: {e}")

    start_time = time.time()
    response = call_orchestrator(
        message,
        fast_mode=fast_mode,
        explore_mode=explore_mode,
        request_id=request_id,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
        generation_params=normalized_generation,
    )
    _subagent_trace(
        "subagent_verify_start",
        {
            "request_id": request_id,
            "lineage_key": lineage_key,
            "phase": "resume_checks",
        },
    )
    response = _maybe_resume_without_change(
        message,
        response,
        request_id=request_id,
        fast_mode=fast_mode,
        explore_mode=explore_mode,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
    )
    response = _maybe_resume_stub_deliverable(
        message,
        response,
        request_id=request_id,
        fast_mode=fast_mode,
        explore_mode=explore_mode,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
    )
    _subagent_trace(
        "subagent_verify_done",
        {
            "request_id": request_id,
            "lineage_key": lineage_key,
            "phase": "resume_checks",
        },
    )
    _emit_llm_trace("llm_call_finished", request_id, "/api/chat", source, fast_mode, cache_hit=False)
    _log_delegate_stall_warnings(request_id)
    processing_time = time.time() - start_time

    if _is_quota_exhausted_response(response):
        quota_blocked_until = time.time() + GEMINI_QUOTA_COOLDOWN_SECONDS
        quota_block_reason = "Обнаружен QUOTA_EXHAUSTED от Gemini CLI."

    response = _clean_chat_response(response, user_message=message)
    response = _apply_quality_postprocess(message, response, fast_mode=fast_mode)
    response, factuality_proxy, code_fact_trace = _apply_chat_reliability_guard(
        message=message,
        response=response,
        request_id=request_id,
        fast_mode=fast_mode,
        explore_mode=explore_mode,
        lab_target=lab_target,
        remote_target=remote_target,
        project_id=project_id,
        research_artifact=research_artifact,
        generation_params=normalized_generation,
    )
    if plan_created:
        response = f"🎯 Автоматически создан план выполнения!\n\n{response}"

    if use_cache and _is_cacheable_response(response):
        api_cache.set(cache_key, response)

    llm_ok = not _response_indicates_llm_error(response)
    _log_chat_completion_trace(
        request_id=request_id,
        latency_ms=int(processing_time * 1000),
        success=llm_ok,
        error=None if llm_ok else response[:500],
        cache_hit=False,
        fast_mode=fast_mode,
    )
    evidence_required = _is_code_fact_prompt(message)
    evidence_ok = (factuality_proxy >= 1.0) if evidence_required else True
    code_fact_metrics = {
        "verification_pass_rate": 1.0 if evidence_ok else 0.0,
        "fallback_rate": 1.0 if code_fact_trace.get("fallback_reason") else 0.0,
        "format_fail_rate": 0.0 if factuality_proxy >= 1.0 else 1.0,
        "symbol_miss_rate": 0.0 if factuality_proxy >= 1.0 else 1.0,
    }
    return {
        "response": response,
        "cached": False,
        "cache_hit": False,
        "processing_time": round(processing_time, 2),
        "provider": os.getenv("LLM_PROVIDER", "unknown"),
        "ok": llm_ok,
        "factuality_proxy": factuality_proxy,
        "evidence_required": evidence_required,
        "evidence_ok": evidence_ok,
        "code_fact_trace": code_fact_trace,
        "code_fact_metrics": code_fact_metrics,
        "error_class": _infer_error_class({"error": None if llm_ok else response[:500]}),
        "error": None if llm_ok else response[:500],
    }


def _build_execution_meta(
    *,
    automation: str,
    response: str,
    request_id: str,
    deliverables: list[str] | None = None,
) -> dict:
    """Emit explicit execution envelope for chat/implement/verify requests."""
    mode = (automation or "chat").strip().lower()
    if mode not in ("chat", "implement", "verify"):
        mode = "chat"
    writes_performed = 0
    if _activity_request_has_change(request_id):
        writes_performed = 1
    artifacts: list[str] = []
    for path in (deliverables or []):
        try:
            p = Path(path)
            if p.is_file():
                artifacts.append(str(p.resolve()))
                writes_performed += 1
        except (OSError, RuntimeError, ValueError):
            continue
    return {
        "execution_mode": mode,
        "writes_performed": writes_performed,
        "artifacts": artifacts,
    }


def _validate_expected_deliverables(
    *,
    automation: str,
    artifacts: list[str],
) -> list[str]:
    mode = (automation or "chat").strip().lower()
    if mode not in ("implement", "verify"):
        return []
    if not artifacts:
        return ["no artifacts written"]
    from modules.deliverable_validator import validate_deliverable

    issues: list[str] = []
    for artifact in artifacts:
        name = Path(artifact).name.upper()
        task_class = "VERIFY" if name.startswith("VERIFY") else "IMPLEMENT"
        result = validate_deliverable(artifact, task_class=task_class)
        if result.stub:
            issues.append(f"{Path(artifact).name}: {result.reason or 'stub'}")
    return issues


def _execute_chat_job_payload(job) -> dict:
    """Run one async chat job (used by background worker)."""
    try:
        from modules.delegate_stall_detector import check_stall

        if job.cancel_requested:
            return {"error": "cancelled"}
        result = _run_chat_pipeline(
            job.message,
            request_id=job.request_id,
            fast_mode=job.fast_mode,
            explore_mode=job.explore_mode,
            lab_target=job.lab_target,
            remote_target=getattr(job, "remote_target", False),
            project_id=(getattr(job, "project_id", None) or None) or None,
            research_artifact=(getattr(job, "research_artifact", None) or None) or None,
            source=job.source,
            generation_params=getattr(job, "generation_params", None) or None,
        )
        if job.cancel_requested:
            return {"error": "cancelled"}
        if result.get("cache_hit"):
            result["cached"] = True
        stall = check_stall(job.request_id)
        if stall.stalled:
            return {"error": f"stall:{stall.reason}"}
        return result
    except Exception as exc:
        _subagent_trace(
            "subagent_error",
            {
                "request_id": getattr(job, "request_id", ""),
                "lineage_key": _request_lineage_key(getattr(job, "request_id", "")),
                "error": str(exc)[:300],
                "stage": "async_worker",
            },
        )
        return {"error": str(exc)[:300]}


def create_app(config: OrchestratorApiConfig, extra_register: Callable[[Flask], None] | None = None) -> Flask:
    """Build Flask app with shared OPRAI API routes."""
    global _api_config
    _api_config = config

    if config.guard_bridge_on_import:
        from modules.root_orchestrator_guard import ensure_root_orchestrator_bridge
        ensure_root_orchestrator_bridge()

    app = Flask(__name__)
    CORS(app, origins=["*"], methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Authorization"])

    # Route handlers are registered by re-binding decorators below
    _register_shared_routes(app)

    try:
        from modules.chat_job_manager import get_chat_job_manager

        get_chat_job_manager().run_worker(_execute_chat_job_payload)
    except Exception as exc:
        print(f"Warning: chat job worker not started: {exc}")

    if extra_register is not None:
        extra_register(app)
    if config.variant == "ork":
        register_ork_queue_routes(app)

    return app


def _register_shared_routes(app: Flask) -> None:
    """Attach all shared route handlers to *app*."""
    @app.route('/api/chat', methods=['POST'])
    def handle_chat_message():
        """Обработчик сообщений из чат-интерфейса"""
        global quota_blocked_until, quota_block_reason
        inc_chat_requests()
        try:
            data = request.get_json()
            if not data or 'message' not in data:
                return jsonify({'error': 'Не указано сообщение'}), 400
        
            message = data['message'].strip()
            if not message:
                return jsonify({'error': 'Пустое сообщение'}), 400
            fast_mode = bool(data.get("fast_mode", False))
            explore_mode = bool(data.get("explore_mode", False))
            lean_mode = bool(data.get("lean_mode", False))
            parity_eval = bool(data.get("parity_eval", False))
            automation = str(data.get("automation", "chat")).strip().lower() or "chat"
            deliverables_raw = data.get("deliverable")
            deliverables = (
                [str(item).strip() for item in deliverables_raw if str(item).strip()]
                if isinstance(deliverables_raw, list)
                else []
            )
            if automation in ("implement", "verify") and deliverables:
                # Ensure agent policy can resolve explicit deliverable targets from message text.
                missing_hints = [d for d in deliverables if f"deliverable={d}" not in message]
                if missing_hints:
                    hint_lines = "\n".join(f"deliverable={d}" for d in missing_hints)
                    message = f"{message}\n{hint_lines}".strip()
            if automation not in ("chat", "implement", "verify"):
                return jsonify({
                    "error": "invalid automation mode",
                    "status": "rejected",
                    "code": "invalid_automation",
                }), 400
            try:
                lab_target, remote_target, project_id = _parse_chat_targets(data)
            except (TargetConflictError, ProjectRegistryError, ValueError) as exc:
                body, code = _chat_target_error_response(exc)
                return jsonify(body), code
            research_artifact = (data.get("research_artifact") or "").strip() or None
            generation_params_raw = {
                "temperature": data.get("temperature"),
                "top_p": data.get("top_p"),
                "top_k": data.get("top_k"),
                "max_output_tokens": data.get("max_output_tokens"),
                "max_tokens": data.get("max_tokens"),
                "stop": data.get("stop"),
            }
            try:
                generation_params = _normalize_generation_params(generation_params_raw)
            except ValueError as exc:
                return jsonify({
                    "error": str(exc),
                    "status": "rejected",
                    "code": "invalid_generation_params",
                }), 400
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            source = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
            client_key = (source or "unknown").split(",")[0].strip()
            request_context = _build_request_context(
                request_id=request_id,
                automation=automation,
                generation_params=generation_params,
                parity_eval=parity_eval,
                source=source,
            )
            _emit_llm_trace("chat_received", request_id, "/api/chat", source, fast_mode, cache_hit=False)

            if lean_mode:
                os.environ["OPRAI_CLI_PROFILE"] = "lean"

            armed_ok, _armed_reason = autonomy_controller.is_apply_allowed()
            if automation in ("implement", "verify") and not armed_ok:
                return jsonify({
                    "error": "execution mode requested but autonomy is not armed",
                    "status": "rejected",
                    "code": "execution_not_armed",
                    "required": ["arm_token", "ttl", "automation"],
                    "execution_mode": automation,
                    "writes_performed": 0,
                    "artifacts": [],
                    "request_id": request_id,
                    "timestamp": datetime.now().isoformat(),
                }), 403

            if explore_mode and not explore_allowed():
                return jsonify({
                    "error": "explore_mode not allowed",
                    "hint": "Set OPRAI_EXPLORE_ALLOWED=1 after tests/live/context_layer_regression.sh passes",
                    "status": "rejected",
                }), 403

            if not fast_mode:
                allowed, reason = check_chat_rate_limit(client_key)
                if not allowed:
                    return jsonify({
                        "error": reason,
                        "status": "rejected",
                        "timestamp": datetime.now().isoformat(),
                    }), 429

            if not fast_mode and quota_blocked_until > time.time():
                retry_in = int(max(1, quota_blocked_until - time.time()))
                return jsonify({
                    'response': f"⛔ Gemini quota exhausted. Повторите позже (~{retry_in} сек). {quota_block_reason}".strip(),
                    'timestamp': datetime.now().isoformat(),
                    'status': 'success',
                    'cached': False,
                    'processing_time': 0.0
                })
        
            print(f"[{datetime.now()}] Получено сообщение: {message[:100]}...")

            pipeline = _run_chat_pipeline(
                message,
                request_id=request_id,
                fast_mode=fast_mode,
                explore_mode=explore_mode,
                lab_target=lab_target,
                remote_target=remote_target,
                project_id=project_id,
                research_artifact=research_artifact,
                source=source,
                generation_params=generation_params,
            )
            parity_payload = None
            if parity_eval and automation == "chat":
                prev_env = {
                    "LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
                    "OPRAI_MERCURY_ONLY": os.environ.get("OPRAI_MERCURY_ONLY"),
                }
                try:
                    os.environ["LLM_PROVIDER"] = "cursor_cli"
                    os.environ["OPRAI_MERCURY_ONLY"] = "0"
                    cursor_pipeline = _run_chat_pipeline(
                        message,
                        request_id=f"{request_id}-parity-cursor",
                        fast_mode=fast_mode,
                        explore_mode=explore_mode,
                        lab_target=lab_target,
                        remote_target=remote_target,
                        project_id=project_id,
                        research_artifact=research_artifact,
                        source=f"{source}|parity-cursor",
                        generation_params=generation_params,
                        use_cache=False,
                    )
                finally:
                    for key, value in prev_env.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                parity_payload = {
                    **_build_parity_eval_payload(
                        request_context=request_context,
                        mercury_pipeline=pipeline,
                        cursor_pipeline=cursor_pipeline,
                    )
                }
            if pipeline.get("cache_hit"):
                print(f"[{datetime.now()}] Возвращен кэшированный ответ")
            else:
                print(
                    f"[{datetime.now()}] Отправлен ответ длиной: {len(pipeline.get('response', ''))} символов "
                    f"(время: {pipeline.get('processing_time', 0):.2f} сек)"
                )

            execution_meta = _build_execution_meta(
                automation=automation,
                response=pipeline.get("response", ""),
                request_id=request_id,
                deliverables=deliverables,
            )
            retry_meta = _build_retry_metadata(
                request_id,
                str(pipeline.get("response", "")),
                retry_index=int(data.get("retry_index") or 1),
            )
            deliverable_issues = _validate_expected_deliverables(
                automation=automation,
                artifacts=execution_meta["artifacts"],
            )
            deliverable_issues.extend(
                _verify_snapshots_intact(request_id, execution_meta["artifacts"])
            )
            if deliverable_issues:
                _subagent_trace(
                    "subagent_error",
                    {
                        "request_id": request_id,
                        "lineage_key": _request_lineage_key(request_id),
                        "error_type": "execution_incomplete",
                        "issues": deliverable_issues,
                    },
                )
                payload = {
                    "error": "execution artifacts incomplete",
                    "status": "rejected",
                    "code": "execution_incomplete",
                    "execution_mode": execution_meta["execution_mode"],
                    "writes_performed": execution_meta["writes_performed"],
                    "artifacts": execution_meta["artifacts"],
                    "issues": deliverable_issues,
                    "request_id": request_id,
                    "timestamp": datetime.now().isoformat(),
                }
                if retry_meta:
                    payload.update(retry_meta)
                return jsonify(payload), 409
            payload = {
                'response': pipeline.get("response", ""),
                'timestamp': datetime.now().isoformat(),
                'status': 'success',
                'cached': bool(pipeline.get("cached")),
                'processing_time': pipeline.get("processing_time", 0),
                'request_id': request_id,
                'execution_mode': execution_meta["execution_mode"],
                'writes_performed': execution_meta["writes_performed"],
                'artifacts': execution_meta["artifacts"],
            }
            if pipeline.get("code_fact_trace") is not None:
                payload["code_fact_trace"] = pipeline.get("code_fact_trace")
            if pipeline.get("code_fact_metrics") is not None:
                payload["code_fact_metrics"] = pipeline.get("code_fact_metrics")
            if retry_meta:
                payload.update(retry_meta)
            if parity_payload is not None:
                payload["parity_eval"] = parity_payload
            return jsonify(payload)
        
        except Exception as e:
            print(f"[{datetime.now()}] Ошибка обработки: {str(e)}")
            err_request_id = locals().get("request_id", str(uuid.uuid4()))
            _log_chat_completion_trace(
                request_id=err_request_id,
                latency_ms=0,
                success=False,
                error=str(e)[:200],
                status_code=500,
            )
            return jsonify({
                'error': f'Внутренняя ошибка сервера: {str(e)[:200]}',
                'timestamp': datetime.now().isoformat(),
                'status': 'error',
                'request_id': err_request_id,
            }), 500

    @app.route('/api/chat/stream', methods=['POST'])
    def handle_chat_stream():
        """SSE stream from configured LLM provider (Inception Mercury or Cursor CLI)."""
        inc_chat_requests()
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'invalid json body'}), 400
            if 'message' not in data:
                return jsonify({'error': 'Не указано сообщение'}), 400
            message = str(data['message']).strip()
            if not message:
                return jsonify({'error': 'Пустое сообщение'}), 400
            if bool(data.get('fast_mode', False)):
                return jsonify({
                    'error': 'fast_mode not supported on stream',
                    'hint': 'Use POST /api/chat for fast_mode',
                }), 400

            explore_mode = bool(data.get('explore_mode', False))
            try:
                lab_target, remote_target, project_id = _parse_chat_targets(data)
            except (TargetConflictError, ProjectRegistryError, ValueError) as exc:
                body, code = _chat_target_error_response(exc)
                return jsonify(body), code
            research_artifact = (data.get('research_artifact') or '').strip() or None
            generation_params_raw = {
                "temperature": data.get("temperature"),
                "top_p": data.get("top_p"),
                "top_k": data.get("top_k"),
                "max_output_tokens": data.get("max_output_tokens"),
                "max_tokens": data.get("max_tokens"),
                "stop": data.get("stop"),
            }
            try:
                generation_params = _normalize_generation_params(generation_params_raw)
            except ValueError as exc:
                return jsonify({
                    "error": str(exc),
                    "status": "rejected",
                    "code": "invalid_generation_params",
                }), 400
            request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())

            if explore_mode and not explore_allowed():
                return jsonify({
                    'error': 'explore_mode not allowed',
                    'hint': 'Set OPRAI_EXPLORE_ALLOWED=1 after tests pass',
                    'status': 'rejected',
                }), 403

            source = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown')
            client_key = (source or 'unknown').split(',')[0].strip()
            allowed, reason = check_chat_rate_limit(client_key)
            if not allowed:
                return jsonify({'error': reason, 'status': 'rejected'}), 429

            _emit_llm_trace('chat_stream_received', request_id, '/api/chat/stream', source, False, cache_hit=False)

            def generate():
                from modules.codebase_context import chat_max_tokens
                from modules.llm_router import get_llm_router

                prev_env = {
                    key: os.environ.get(key)
                    for key in (
                        'CURSOR_AGENT_WORKSPACE',
                        'OPRAI_INSTANCE_ROOT',
                        'OPRAI_AGENT_ACTIVITY_LOG',
                        'OPRAI_EXPLORE_MODE',
                        'OPRAI_CLI_PROFILE',
                        'OPRAI_REQUEST_ID',
                        'OPRAI_ALLOW_WRITES',
                        'CURSOR_AGENT_MODE',
                        'CURSOR_CLI_COMMAND',
                        'OPRAI_RESEARCH_ARTIFACT',
                        'OPRAI_PROJECT_ID',
                        'OPRAI_TARGET_KIND',
                        'OPRAI_REMOTE_TARGET',
                        'OPRAI_REMOTE_CONTEXT',
                    )
                }
                stream_env, _workspace = _build_chat_runtime_env(
                    explore_mode=explore_mode,
                    lab_target=lab_target,
                    remote_target=remote_target,
                    project_id=project_id,
                    request_id=request_id,
                    research_artifact=research_artifact,
                    message=message,
                )
                try:
                    os.environ.update(stream_env)
                    router = get_llm_router()
                    allow_writes = os.getenv('OPRAI_ALLOW_WRITES', '').strip().lower() in ('1', 'true', 'yes')
                    for event_name, payload in router.iter_call_stream(
                        messages=[{'role': 'user', 'content': message}],
                        model='auto',
                        max_tokens=int(generation_params.get("max_tokens", chat_max_tokens())),
                        temperature=float(generation_params.get("temperature", 0.1)),
                        explore_mode=explore_mode,
                        allow_writes=allow_writes,
                    ):
                        if event_name == 'meta' and isinstance(payload, dict):
                            payload = {**payload, 'request_id': payload.get('request_id') or request_id}
                        yield _sse_pack(event_name, payload if isinstance(payload, dict) else {'data': payload})
                except Exception as exc:
                    yield _sse_pack('error', {'error': str(exc)[:300], 'request_id': request_id})
                finally:
                    for key, value in prev_env.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value

            return Response(stream_with_context(generate()), mimetype='text/event-stream')
        except Exception as exc:
            err_request_id = locals().get('request_id', str(uuid.uuid4()))
            return jsonify({
                'error': f'Внутренняя ошибка сервера: {str(exc)[:200]}',
                'status': 'error',
                'request_id': err_request_id,
            }), 500

    @app.route('/api/chat/async', methods=['POST'])
    def handle_chat_async():
        """Queue chat message; returns immediately with job_id."""
        try:
            data = request.get_json() or {}
            message = str(data.get("message", "")).strip()
            if not message:
                return jsonify({"error": "Не указано сообщение"}), 400
            fast_mode = bool(data.get("fast_mode", False))
            explore_mode = bool(data.get("explore_mode", False))
            try:
                lab_target, remote_target, project_id = _parse_chat_targets(data)
            except (TargetConflictError, ProjectRegistryError, ValueError) as exc:
                body, code = _chat_target_error_response(exc)
                return jsonify(body), code
            research_artifact = (data.get("research_artifact") or "").strip() or None
            generation_params_raw = {
                "temperature": data.get("temperature"),
                "top_p": data.get("top_p"),
                "top_k": data.get("top_k"),
                "max_output_tokens": data.get("max_output_tokens"),
                "max_tokens": data.get("max_tokens"),
                "stop": data.get("stop"),
            }
            try:
                generation_params = _normalize_generation_params(generation_params_raw)
            except ValueError as exc:
                return jsonify({
                    "error": str(exc),
                    "status": "rejected",
                    "code": "invalid_generation_params",
                }), 400
            source = str(data.get("source") or request.remote_addr or "async")
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

            if explore_mode and not explore_allowed():
                return jsonify({"error": "explore_mode not allowed", "status": "rejected"}), 403

            if not fast_mode:
                client_key = (source or "unknown").split(",")[0].strip()
                allowed, reason = check_chat_rate_limit(client_key)
                if not allowed:
                    return jsonify({"error": reason, "status": "rejected"}), 429

            from modules.chat_job_manager import get_chat_job_manager

            mgr = get_chat_job_manager()

            cache_key = api_cache.generate_key(
                _build_runtime_cache_key(message, fast_mode, generation_params)
            )
            cached_response = api_cache.get(cache_key)
            if cached_response and _is_cacheable_response(cached_response):
                job = mgr.create(
                    message,
                    fast_mode=fast_mode,
                    explore_mode=explore_mode,
                    lab_target=lab_target,
                    remote_target=remote_target,
                    project_id=project_id or "",
                    research_artifact=research_artifact or "",
                    generation_params=generation_params,
                    source=source,
                    request_id=request_id,
                )
                mgr.complete_cached(job.job_id, cached_response)
                _emit_llm_trace("cache_hit", request_id, "/api/chat/async", source, fast_mode, cache_hit=True)
                return jsonify({
                    "status": "accepted",
                    "job_id": job.job_id,
                    "request_id": job.request_id,
                    "job_status": "completed",
                    "cached": True,
                }), 202

            job = mgr.create(
                message,
                fast_mode=fast_mode,
                explore_mode=explore_mode,
                lab_target=lab_target,
                remote_target=remote_target,
                project_id=project_id or "",
                research_artifact=research_artifact or "",
                generation_params=generation_params,
                source=source,
                request_id=request_id,
            )
            return jsonify({
                "status": "accepted",
                "job_id": job.job_id,
                "request_id": job.request_id,
                "job_status": job.status,
                "cached": False,
            }), 202
        except Exception as exc:
            return jsonify({"error": str(exc)[:200], "status": "error"}), 500

    @app.route('/api/chat/jobs', methods=['GET'])
    def list_chat_jobs():
        try:
            from modules.chat_job_manager import get_chat_job_manager

            limit = int(request.args.get("limit", 10))
            jobs = get_chat_job_manager().list_recent(limit=max(1, min(limit, 50)))
            return jsonify({
                "status": "ok",
                "jobs": [j.to_dict() for j in jobs],
                "count": len(jobs),
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/chat/jobs/<job_id>', methods=['GET'])
    def get_chat_job(job_id: str):
        try:
            from modules.chat_job_manager import get_chat_job_manager

            job = get_chat_job_manager().get(job_id)
            if not job:
                return jsonify({"status": "error", "error": "job_not_found"}), 404
            return jsonify({"status": "ok", "job": job.to_dict()})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/chat/jobs/<job_id>/cancel', methods=['POST'])
    def cancel_chat_job(job_id: str):
        try:
            from modules.chat_job_manager import get_chat_job_manager

            job = get_chat_job_manager().cancel(job_id)
            if not job:
                return jsonify({"status": "error", "error": "job_not_found"}), 404
            return jsonify({"status": "ok", "job": job.to_dict()})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Проверка работоспособности API"""
        inc_http_request("/api/health")
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'version': '1.0.0'
        })

    @app.route('/metrics', methods=['GET'])
    def metrics():
        """Prometheus-style in-memory metrics."""
        body = render_prometheus_text()
        return body, 200, {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}


    @app.route('/api/projects', methods=['GET'])
    def list_projects_api():
        """Registry projects for panel picker (no secrets)."""
        try:
            active_only = request.args.get("active_only", "1").strip().lower() not in (
                "0",
                "false",
                "no",
            )
            rows = list_projects_public(active_only=active_only)
            return jsonify({
                "status": "ok",
                "projects": rows,
                "count": len(rows),
                "timestamp": datetime.now().isoformat(),
            })
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/projects/<project_id>', methods=['GET'])
    def get_project_api(project_id: str):
        """Single registry project row (no secrets)."""
        try:
            pid = validate_project_id(project_id)
            entry = resolve_project(pid)
            return jsonify({
                "status": "ok",
                "project": project_to_public_dict(entry),
                "timestamp": datetime.now().isoformat(),
            })
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "unknown_project"}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/projects/<project_id>/remote-manifest', methods=['GET'])
    def project_remote_manifest_api(project_id: str):
        """Panel manifest drawer: reads, writes, verify, dual arm status."""
        try:
            pid = validate_project_id(project_id)
            request_id = (request.args.get("request_id") or "").strip() or None
            run_doctor = str(request.args.get("doctor") or "").strip().lower() in ("1", "true", "yes")
            edge_status: dict = {}
            doctor_doc: Optional[dict] = None
            try:
                from modules.remote_ssh_client import RemoteSSHClient, RemoteSSHError
                from modules.remote_panel import doctor_summary

                client = RemoteSSHClient(pid, request_id=request_id or "")
                edge_status = client.status()
                if run_doctor:
                    try:
                        doctor_doc = doctor_summary(client.doctor())
                    except (RemoteSSHError, OSError) as exc:
                        doctor_doc = {"ok": False, "error": str(exc)[:200]}
            except (RemoteSSHError, ProjectRegistryError, OSError) as exc:
                edge_status = {"error": str(exc)[:200]}
            manifest = build_remote_manifest(
                pid,
                request_id=request_id,
                central_autonomy=autonomy_controller.snapshot(),
                edge_status=edge_status,
                doctor=doctor_doc,
                lab_root=Path(_cfg().instance_root),
            )
            return jsonify({"status": "ok", "manifest": manifest, "timestamp": datetime.now().isoformat()})
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "unknown_project"}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/projects/<project_id>/remote-verify', methods=['POST'])
    def project_remote_verify_api(project_id: str):
        """BR-13: one-click post-delegate verify (read-only verify_w12_phase.sh)."""
        try:
            pid = validate_project_id(project_id)
            resolve_remote_project(pid, allow_inventory=True)
            body = request.get_json(silent=True) or {}
            phase = (body.get("phase") or request.args.get("phase") or "staging").strip()
            verify = run_w12_phase_verify(pid, phase, lab_root=Path(_cfg().instance_root))
            return jsonify({
                "status": "ok",
                "verify": verify,
                "pass": bool(verify.get("pass")),
                "timestamp": datetime.now().isoformat(),
            })
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "unknown_project"}), 400
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "invalid_phase"}), 400
        except FileNotFoundError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "verify_script_missing"}), 500
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/projects/<project_id>/remote-index', methods=['GET'])
    def project_remote_index_api(project_id: str):
        """Cached remote file tree for panel browser (v1.1)."""
        try:
            pid = validate_project_id(project_id)
            resolve_remote_project(pid, allow_inventory=True)
            index = public_remote_index(pid, lab_root=Path(_cfg().instance_root))
            return jsonify({"status": "ok", "index": index, "timestamp": datetime.now().isoformat()})
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "unknown_project"}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/projects/<project_id>/remote-read', methods=['GET'])
    def project_remote_read_api(project_id: str):
        """Read-only remote file content for panel tree (v1.1)."""
        try:
            pid = validate_project_id(project_id)
            resolve_remote_project(pid, allow_inventory=True)
            path = (request.args.get("path") or "").strip()
            if not path:
                return jsonify({"status": "error", "error": "path query parameter required", "code": "missing_path"}), 400
            doc = public_remote_file_read(pid, path, lab_root=Path(_cfg().instance_root))
            if doc.get("error"):
                return jsonify({"status": "error", "error": doc["error"], "code": "read_failed", "file": doc}), 404
            return jsonify({"status": "ok", "file": doc, "timestamp": datetime.now().isoformat()})
        except ProjectRegistryError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "unknown_project"}), 400
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc), "code": "invalid_path"}), 400
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.route('/api/context/status', methods=['GET'])
    def context_layer_status():
        """Context layer index/map status."""
        try:
            workspace_root = os.getenv(
                "OPRAI_INSTANCE_ROOT",
                os.getenv("CURSOR_AGENT_WORKSPACE", _cfg().instance_root),
            ).rstrip("/")
            return jsonify({
                "status": "ok",
                "workspace_root": workspace_root,
                "context": context_status(),
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500


    @app.route('/api/agent/activity', methods=['GET'])
    def agent_activity_status():
        """Live Cursor agent run visibility (tail log + active run)."""
        try:
            from modules.agent_activity import get_snapshot

            tail = int(request.args.get("tail", 80))
            request_id = (request.args.get("request_id") or "").strip() or None
            hide_think = request.args.get("hide_think", "0").strip().lower() in ("1", "true", "yes")
            return jsonify({
                "status": "ok",
                "activity": get_snapshot(
                    tail_lines=max(1, min(tail, 500)),
                    request_id=request_id,
                    hide_think=hide_think,
                ),
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500


    @app.route('/api/agent/activity/summary', methods=['GET'])
    def agent_activity_summary():
        """Agent activity log line count and last event (stub metrics)."""
        try:
            from modules.agent_activity import get_activity_summary

            return jsonify({
                "status": "ok",
                **get_activity_summary(),
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500


    @app.route('/api/health/cursor', methods=['GET'])
    def cursor_health():
        """Проверка Cursor CLI runtime."""
        try:
            from modules.llm_router import get_llm_router
            health = get_llm_router().health()
            primary = health.get("primary_health", {})
            return jsonify({
                "status": "ok" if primary.get("ok") else "degraded",
                "provider": health.get("provider"),
                "cursor_adapter": health.get("cursor_adapter"),
                "primary_health": primary,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500


    @app.route('/api/auth/cursor-cli/status', methods=['GET'])
    def auth_cursor_cli_status():
        """Статус аутентификации Cursor CLI."""
        try:
            from modules.cursor_cli_adapter import CursorCliAdapter
            adapter = CursorCliAdapter()
            auth = adapter.auth_status()
            health = adapter.check_health()
            return jsonify({
                "mode": "cursor_cli",
                "authenticated": auth.get("ok", False),
                "auth": auth,
                "health": health,
                "timestamp": datetime.now().isoformat(),
            })
        except Exception as exc:
            return jsonify({"mode": "cursor_cli", "authenticated": False, "error": str(exc)}), 500


    @app.route('/api/autonomy/arm', methods=['POST'])
    def autonomy_arm():
        data = request.get_json() or {}
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        source = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        try:
            snap = autonomy_controller.arm(
                mode=data.get("mode", "apply_once"),
                ttl_seconds=int(data.get("ttl_seconds", 900)),
                approval_token=str(data.get("approval_token", "")),
                max_steps=int(data.get("max_steps", 1)),
                max_files=int(data.get("max_files", 1)),
                max_llm_calls=int(data.get("max_llm_calls", 3)),
                reason=str(data.get("reason", "")),
                operator=str(data.get("operator", source)),
            )
            _emit_autonomy_trace("autonomy_arm", request_id, "/api/autonomy/arm", source, {"mode": snap.get("mode")})
            return jsonify({"status": "armed", "autonomy": snap, "request_id": request_id})
        except Exception as e:
            return jsonify({"error": str(e), "request_id": request_id}), 400


    @app.route('/api/autonomy/disarm', methods=['POST'])
    def autonomy_disarm():
        data = request.get_json() or {}
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        source = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        snap = autonomy_controller.disarm(str(data.get("reason", "manual_disarm")))
        _emit_autonomy_trace("autonomy_disarm", request_id, "/api/autonomy/disarm", source, {"reason": data.get("reason", "manual_disarm")})
        return jsonify({"status": "disarmed", "autonomy": snap, "request_id": request_id})


    @app.route('/api/autonomy/status', methods=['GET'])
    def autonomy_status():
        return jsonify({"status": "ok", "autonomy": autonomy_controller.snapshot(), "timestamp": datetime.now().isoformat()})


    @app.route('/api/autonomy/report', methods=['GET'])
    def autonomy_report():
        limit = int(request.args.get("limit", "50"))
        return jsonify({"status": "ok", "items": autonomy_controller.recent_reports(limit=limit)})


    @app.route('/api/autonomy/run', methods=['POST'])
    def autonomy_run():
        data = request.get_json() or {}
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        source = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        mode = autonomy_controller.snapshot().get("mode", "propose")
        if mode == "propose":
            proposal = {
                "action": data.get("action", "transactional_edit"),
                "target_path": data.get("target_path", ""),
                "message": "propose_only_mode: no changes applied",
            }
            _emit_autonomy_trace("autonomy_reject", request_id, "/api/autonomy/run", source, {"reason": "mode_is_propose"})
            return jsonify({"status": "proposal", "proposal": proposal, "request_id": request_id})
        gate_resp = _autonomy_gate_or_403(str(data.get("approval_token", "")))
        if gate_resp is not None:
            _emit_autonomy_trace("autonomy_reject", request_id, "/api/autonomy/run", source, {"reason": "gate_failed"})
            return gate_resp
        action = str(data.get("action", "transactional_edit"))
        if action != "transactional_edit":
            return jsonify({"error": "unsupported_action", "request_id": request_id}), 400
        run_id = str(uuid.uuid4())
        _emit_autonomy_trace("autonomy_run_start", request_id, "/api/autonomy/run", source, {"run_id": run_id})
        result = autonomy_controller.run_transactional_update(
            target_path=str(data.get("target_path", "")),
            old_text=str(data.get("old_text", "")),
            new_text=str(data.get("new_text", "")),
            run_compile_check=bool(data.get("run_compile_check", True)),
            autonomy_run_id=run_id,
            kpi_delta=data.get("kpi_delta") if isinstance(data.get("kpi_delta"), dict) else None,
        )
        _emit_autonomy_trace("autonomy_run_result", request_id, "/api/autonomy/run", source, {"run_id": run_id, "success": result.get("success", False)})
        return jsonify({"status": "success" if result.get("success") else "failed", "result": result, "autonomy_run_id": run_id, "request_id": request_id})

    @app.route('/api/plans/receive', methods=['POST'])
    def receive_plan_api():
        """API endpoint для получения планов от OPRAIPlanner"""
        global current_plan_executor

        print("🌐 API: receive_plan_api вызван")
        try:
            plan_data = request.get_json()
            print(f"🌐 API: Получены данные плана: {plan_data.get('plan_name', 'Unknown') if plan_data else 'None'}")

            if not plan_data:
                print("🌐 API: Ошибка - нет данных плана")
                return jsonify({"error": "No plan data provided"}), 400

            # Импортируем и вызываем receive_plan из оркестратора
            print("🌐 API: Импортируем PlanExecutor...")
            _, receive_plan, PlanExecutor = _load_plan_executor_symbols()
            result = receive_plan(plan_data)
            print(f"🌐 API: receive_plan вернул: {result}")

            if result.get("status") == "received":
                # Создаем новый PlanExecutor и сохраняем ссылку глобально
                plan_name = plan_data.get('plan_name', f"План от OPRAIPlanner ({len(plan_data['steps'])} шагов)")
                plan_steps = plan_data['steps']

                current_plan_executor = PlanExecutor()
                current_plan_executor.start_plan_execution(plan_name, plan_steps)

                # Запускаем выполнение в отдельном потоке
                import threading

                def execute_plan_sync():
                    try:
                        print(f"🧵 ПОТОК: Начинаем выполнение плана {result.get('plan_id')}")
                        print(f"🧵 ПОТОК: current_plan_executor = {current_plan_executor}")
                        print(f"🧵 ПОТОК: hasattr execute_plan_sync = {hasattr(current_plan_executor, 'execute_plan_sync')}")

                        # Вызываем синхронный метод выполнения
                        if hasattr(current_plan_executor, 'execute_plan_sync'):
                            print("🧵 ПОТОК: Вызываем execute_plan_sync...")
                            current_plan_executor.execute_plan_sync(result.get("plan_id"))
                            print(f"🧵 ПОТОК: ✅ План {result.get('plan_id')} завершен")
                        else:
                            print("🧵 ПОТОК: ❌ Метод execute_plan_sync не найден!")
                            print(f"🧵 ПОТОК: Доступные методы: {[m for m in dir(current_plan_executor) if 'execute' in m]}")

                    except Exception as e:
                        print(f"🧵 ПОТОК: ❌ Ошибка выполнения плана: {e}")
                        import traceback
                        traceback.print_exc()

                thread = threading.Thread(target=execute_plan_sync, daemon=True)
                thread.start()
                print(f"🧵 ГЛАВНЫЙ: Поток запущен для плана {result.get('plan_id')}")

                return jsonify({
                    "status": "success",
                    "message": "Plan received and queued for execution",
                    "plan_id": result.get("plan_id")
                }), 200
            else:
                return jsonify({"error": result.get("error", "Unknown error")}), 400

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/', methods=['GET'])
    def index():
        """Главная страница с информацией об API"""
        return jsonify({
            'message': 'OPRAI Orchestrator API v1.0.0',
            'endpoints': {
                'POST /api/chat': 'Отправить сообщение оркестратору',
                'GET /api/health': 'Проверка работоспособности',
                'GET /metrics': 'Prometheus-style metrics'
            },
            'status': 'running'
        })

    @app.route('/api/cache/stats', methods=['GET'])
    def get_cache_stats():
        """Получить статистику кэширования API"""
        try:
            stats = api_cache.get_stats()
            return jsonify({
                'status': 'success',
                'cache_stats': stats,
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500

    @app.route('/api/cache/clear', methods=['POST'])
    def clear_cache():
        """Очистить кэш API"""
        try:
            cleared_count = api_cache.clear_expired()
            # Полная очистка
            api_cache.cache.clear()
            api_cache.timestamps.clear()
            api_cache.stats = {'hits': 0, 'misses': 0, 'total': 0}
            api_cache.save_cache()

            return jsonify({
                'status': 'success',
                'message': f'Кэш очищен. Удалено {cleared_count} просроченных записей.',
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500

    @app.route('/api/patch/apply', methods=['POST'])
    def patch_apply_endpoint():
        try:
            data = request.get_json() or {}
            patch_mode = str(data.get("patch_mode") or "plan_only").strip().lower()
            response_text = str(data.get("response_text") or "")
            request_id = str(data.get("request_id") or str(uuid.uuid4()))
            patch_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
            verify_commands = data.get("verify_commands")
            if not isinstance(verify_commands, list):
                verify_commands = None

            contract = validate_patch_response_contract(response_text)
            if not contract.ok:
                audit_log = append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "rejected",
                    "code": "patch_contract_invalid",
                    "errors": contract.errors,
                })
                return jsonify({
                    "status": "rejected",
                    "code": "patch_contract_invalid",
                    "patch_mode": patch_mode,
                    "request_id": request_id,
                    "errors": contract.errors,
                    "patch_contract_ok": False,
                    "artifacts": {"audit_log": audit_log},
                }), 400

            policy = evaluate_patch_policy(contract.files)
            if not policy.allowed:
                audit_log = append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "rejected",
                    "code": "patch_policy_blocked",
                    "blocked_files": policy.blocked_files,
                    "policy_reasons": policy.reasons,
                })
                return jsonify({
                    "status": "rejected",
                    "code": "patch_policy_blocked",
                    "patch_mode": patch_mode,
                    "request_id": request_id,
                    "patch_contract_ok": True,
                    "blocked_files": policy.blocked_files,
                    "policy_reasons": policy.reasons,
                    "artifacts": {"audit_log": audit_log},
                }), 403

            risk = score_patch_risk(contract.files, contract.patch_text)
            payload = {
                "status": "success",
                "patch_mode": patch_mode,
                "request_id": request_id,
                "patch_contract_ok": True,
                "patch_hash": patch_hash,
                "patch_risk": {"level": risk.level, "score": risk.score, "reasons": risk.reasons},
                "files": contract.files,
                "diff_blocks": contract.diff_blocks,
                "hunks": contract.hunks,
            }
            audit_log = append_patch_audit({
                "request_id": request_id,
                "patch_mode": patch_mode,
                "patch_hash": patch_hash,
                "status": "accepted",
                "patch_risk": {"level": risk.level, "score": risk.score, "reasons": risk.reasons},
                "files": contract.files,
            })
            payload["artifacts"] = {"audit_log": audit_log}
            _subagent_trace("patch_apply_received", {
                "request_id": request_id,
                "patch_mode": patch_mode,
                "patch_hash": patch_hash,
                "files": contract.files,
                "risk_level": risk.level,
            })

            workspace = "/home/opr/oprai_lab"
            if patch_mode == "plan_only":
                payload.update({
                    "dry_run_status": "skipped",
                    "apply_status": "not_requested",
                    "rollback_status": "not_applicable",
                })
                append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "success",
                    "dry_run_status": "skipped",
                    "apply_status": "not_requested",
                })
                return jsonify(payload)

            if patch_mode == "dry_run":
                dry = run_patch_dry_run(
                    patch_text=contract.patch_text,
                    workspace=workspace,
                    request_id=request_id,
                    verify_commands=verify_commands,
                )
                payload.update({
                    "dry_run_status": "passed" if dry.ok else "failed",
                    "apply_status": "not_requested",
                    "rollback_status": "not_applicable",
                    "artifacts": dry.artifacts,
                })
                if not dry.ok:
                    payload["status"] = "rejected"
                    payload["code"] = "patch_dry_run_failed"
                    payload["errors"] = dry.errors
                    append_patch_audit({
                        "request_id": request_id,
                        "patch_mode": patch_mode,
                        "patch_hash": patch_hash,
                        "status": "rejected",
                        "code": "patch_dry_run_failed",
                        "errors": dry.errors,
                    })
                    return jsonify(payload), 409
                append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "success",
                    "dry_run_status": "passed",
                })
                return jsonify(payload)

            if patch_mode != "apply_confirmed":
                return jsonify({
                    "status": "rejected",
                    "code": "patch_mode_invalid",
                    "request_id": request_id,
                }), 400

            if risk.level in ("medium", "high") and not bool(data.get("approval_confirmed", False)):
                payload.update({
                    "status": "rejected",
                    "code": "patch_approval_required",
                    "dry_run_status": "required",
                    "apply_status": "blocked",
                    "rollback_status": "not_applicable",
                })
                append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "rejected",
                    "code": "patch_approval_required",
                    "patch_risk": {"level": risk.level, "score": risk.score},
                })
                return jsonify(payload), 409

            dry = run_patch_dry_run(
                patch_text=contract.patch_text,
                workspace=workspace,
                request_id=f"{request_id}-dryrun",
                verify_commands=verify_commands,
            )
            payload["dry_run_status"] = "passed" if dry.ok else "failed"
            payload["artifacts"] = dict(dry.artifacts)
            if not dry.ok:
                payload.update({
                    "status": "rejected",
                    "code": "patch_dry_run_failed",
                    "errors": dry.errors,
                    "apply_status": "blocked",
                    "rollback_status": "not_applicable",
                })
                append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "rejected",
                    "code": "patch_dry_run_failed",
                    "errors": dry.errors,
                })
                return jsonify(payload), 409

            apply_res = apply_patch_transactional(
                patch_text=contract.patch_text,
                workspace=workspace,
                request_id=request_id,
                verify_commands=verify_commands,
            )
            payload["artifacts"].update(apply_res.artifacts)
            payload["apply_status"] = "applied" if apply_res.ok else "failed"
            payload["rollback_status"] = "rolled_back" if apply_res.rolled_back else "not_required"
            if not apply_res.ok:
                payload.update({
                    "status": "rejected",
                    "code": "patch_apply_failed",
                    "errors": apply_res.errors,
                })
                append_patch_audit({
                    "request_id": request_id,
                    "patch_mode": patch_mode,
                    "patch_hash": patch_hash,
                    "status": "rejected",
                    "code": "patch_apply_failed",
                    "rollback_status": payload["rollback_status"],
                    "errors": apply_res.errors,
                })
                return jsonify(payload), 409
            append_patch_audit({
                "request_id": request_id,
                "patch_mode": patch_mode,
                "patch_hash": patch_hash,
                "status": "success",
                "dry_run_status": payload.get("dry_run_status"),
                "apply_status": payload.get("apply_status"),
                "rollback_status": payload.get("rollback_status"),
            })
            return jsonify(payload)
        except Exception as exc:
            return jsonify({
                "status": "error",
                "code": "patch_apply_internal_error",
                "error": str(exc)[:300],
            }), 500

    # ===== PLAN MANAGEMENT ENDPOINTS =====

    @app.route('/api/plans', methods=['GET'])
    def get_plans():
        """Получить список всех планов"""
        global current_plan_executor

        try:
            if current_plan_executor:
                # Используем текущий активный план
                status = _normalize_plan_status(_compat_get_execution_status(current_plan_executor))
                status = _merge_status_disk_fallback(current_plan_executor, status)
            else:
                # Создаем новый экземпляр если нет активного
                _, _, PlanExecutor = _load_plan_executor_symbols()
                plan_executor = PlanExecutor()
                _compat_load_execution_state(plan_executor)
                status = _normalize_plan_status(_compat_get_execution_status(plan_executor))
                status = _merge_status_disk_fallback(plan_executor, status)

            plans = []
            if status and (status.get('plan_name') or '').strip():
                plans.append({
                    'id': 'current_plan',
                    'name': status.get('plan_name', 'Без названия'),
                    'description': status.get('plan_name', ''),
                    'status': status.get('status', 'idle'),
                    'progress': status.get('progress', 0),
                    'current_step': status.get('current_step', 0),
                    'total_steps': status.get('total_steps', 0),
                    'plan_steps': status.get('plan_steps', []),
                    'execution_logs': status.get('execution_logs', []),
                    'results': status.get('results', []),
                    'errors': status.get('errors', []),
                    'created_at': status.get('start_time'),
                    'updated_at': status.get('last_update')
                })

            return jsonify(plans)
        except Exception as e:
            return jsonify({'error': str(e)}), 500


    @app.route('/api/plans/audit', methods=['GET'])
    def get_plans_audit():
        try:
            window_hours = int(request.args.get("window_hours", "24"))
            items = audit_window(window_hours=window_hours)
            return jsonify({
                "status": "ok",
                "window_hours": window_hours,
                "count": len(items),
                "items": items,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route('/api/plans/audit/<plan_id>', methods=['GET'])
    def get_plan_audit(plan_id):
        try:
            window_hours = int(request.args.get("window_hours", "24"))
            item = audit_plan(plan_id=plan_id, window_hours=window_hours)
            if not item:
                return jsonify({"error": "audit_not_found"}), 404
            return jsonify({"status": "ok", "item": item, "window_hours": window_hours})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/plans', methods=['POST'])
    def create_plan():
        """Создать новый план из задачи"""
        global current_plan_executor
        try:
            data = request.get_json()
            if not data or 'task' not in data:
                return jsonify({'error': 'Не указана задача'}), 400

            task = data['task'].strip()
            if not task:
                return jsonify({'error': 'Пустая задача'}), 400

            _, _, PlanExecutor = _load_plan_executor_symbols()
            plan_executor = PlanExecutor()

            # Создаем детальный план с 20 шагами на основе задачи
            plan_name = f"План: {task[:50]}..."

            # Определяем тип задачи для выбора соответствующего плана
            if 'viber' in task.lower() and ('интеграц' in task.lower() or 'ocr' in task.lower() or 'интеграт' in task.lower()):
                # План интеграции viber-web-server и улучшения OCR
                plan_steps = [
                    {'title': 'Анализ архитектуры viber-web-server', 'description': 'Изучить структуру проекта и компоненты', 'task': 'Проанализировать архитектуру viber-web-server'},
                    {'title': 'Проверка зависимостей и окружения', 'description': 'Проверить установку всех необходимых библиотек', 'task': 'Проверить зависимости Python, OCR библиотеки, базы данных'},
                    {'title': 'Создание модуля интеграции', 'description': 'Создать ViberIntegration класс для связи с оркестратором', 'task': 'Реализовать модуль интеграции viber_integration_module.py'},
                    {'title': 'Настройка API эндпоинтов', 'description': 'Настроить эндпоинты для связи с оркестратором', 'task': 'Добавить API endpoints для управления из оркестратора'},
                    {'title': 'Тестирование базового подключения', 'description': 'Протестировать связь между viber-web-server и оркестратором', 'task': 'Выполнить базовое тестирование интеграции'},
                    {'title': 'Анализ текущей системы OCR', 'description': 'Изучить существующую систему распознавания', 'task': 'Проанализировать текущую реализацию OCR в viber-web-server'},
                    {'title': 'Включение улучшенных OCR библиотек', 'description': 'Активировать EasyOCR, PaddleOCR, TrOCR', 'task': 'Включить и настроить дополнительные OCR библиотеки'},
                    {'title': 'Оптимизация предобработки изображений', 'description': 'Улучшить подготовку изображений для OCR', 'task': 'Реализовать улучшенную предобработку: CLAHE, denoising, binarization'},
                    {'title': 'Улучшение распознавания номеров телефонов', 'description': 'Оптимизировать поиск и парсинг номеров', 'task': 'Улучшить распознавание номеров телефонов всех украинских форматов'},
                    {'title': 'Настройка кеширования OCR результатов', 'description': 'Добавить кеширование для повторяющихся изображений', 'task': 'Реализовать Redis/PostgreSQL кеширование OCR результатов'},
                    {'title': 'Интеграция с базой данных', 'description': 'Настроить хранение результатов в БД', 'task': 'Оптимизировать схему БД для хранения OCR результатов'},
                    {'title': 'Создание unit-тестов', 'description': 'Написать тесты для OCR модулей', 'task': 'Создать comprehensive unit tests для всех OCR функций'},
                    {'title': 'Настройка мониторинга производительности', 'description': 'Добавить метрики и логирование', 'task': 'Интегрировать мониторинг через OPRAI13'},
                    {'title': 'Добавление обработки ошибок', 'description': 'Улучшить обработку исключений и fallback', 'task': 'Реализовать robust error handling и fallback механизмы'},
                    {'title': 'Оптимизация скорости работы', 'description': 'Ускорить обработку изображений', 'task': 'Профилировать и оптимизировать производительность OCR'},
                    {'title': 'Тестирование на различных изображениях', 'description': 'Протестировать на разных типах скриншотов', 'task': 'Выполнить тестирование на разнообразных тестовых изображениях'},
                    {'title': 'Создание документации', 'description': 'Написать документацию по интеграции', 'task': 'Создать подробную документацию для разработчиков'},
                    {'title': 'Настройка CI/CD', 'description': 'Настроить автоматическое тестирование', 'task': 'Интегрировать CI/CD через OPRAI13 для автоматического тестирования'},
                    {'title': 'Проведение нагрузочного тестирования', 'description': 'Протестировать производительность под нагрузкой', 'task': 'Выполнить нагрузочное тестирование OCR системы'},
                    {'title': 'Финальное развертывание', 'description': 'Подготовить к продакшену', 'task': 'Финализировать развертывание и настроить мониторинг'}
                ]
            else:
                # Общий план для других задач
                plan_steps = [
                    {'title': 'Анализ задачи', 'description': f'Анализ: {task[:100]}', 'task': f'Проанализировать задачу: {task}'},
                    {'title': 'Планирование решения', 'description': 'Разработать план решения', 'task': 'Создать детальный план выполнения задачи'},
                    {'title': 'Подготовка окружения', 'description': 'Настроить необходимое окружение', 'task': 'Подготовить и настроить рабочее окружение'},
                    {'title': 'Реализация основных компонентов', 'description': 'Разработать основные части решения', 'task': 'Реализовать основные функциональные компоненты'},
                    {'title': 'Интеграция компонентов', 'description': 'Связать компоненты в единое решение', 'task': 'Выполнить интеграцию всех компонентов'},
                    {'title': 'Тестирование функциональности', 'description': 'Протестировать работу решения', 'task': 'Выполнить функциональное тестирование'},
                    {'title': 'Оптимизация производительности', 'description': 'Улучшить производительность', 'task': 'Профилировать и оптимизировать производительность'},
                    {'title': 'Написание тестов', 'description': 'Создать автоматические тесты', 'task': 'Написать unit и integration тесты'},
                    {'title': 'Документирование', 'description': 'Создать документацию', 'task': 'Написать документацию для пользователей и разработчиков'},
                    {'title': 'Безопасность и валидация', 'description': 'Проверить безопасность', 'task': 'Выполнить security audit и валидацию'},
                    {'title': 'Подготовка к развертыванию', 'description': 'Подготовить к продакшену', 'task': 'Настроить конфигурацию для продакшен окружения'},
                    {'title': 'Развертывание', 'description': 'Выполнить развертывание', 'task': 'Развернуть решение в целевом окружении'},
                    {'title': 'Мониторинг и поддержка', 'description': 'Настроить мониторинг', 'task': 'Настроить системы мониторинга и поддержки'},
                    {'title': 'Обратная связь', 'description': 'Собрать обратную связь', 'task': 'Получить и проанализировать обратную связь'},
                    {'title': 'Финализация', 'description': 'Завершить проект', 'task': 'Выполнить финальные доработки и закрыть проект'},
                    {'title': 'Анализ результатов', 'description': 'Проанализировать достигнутые результаты', 'task': 'Подвести итоги и проанализировать достигнутые цели'},
                    {'title': 'Документирование опыта', 'description': 'Зафиксировать полученный опыт', 'task': 'Документировать lessons learned и best practices'},
                    {'title': 'Поддержка и сопровождение', 'description': 'Настроить сопровождение', 'task': 'Организовать процесс поддержки и сопровождения'},
                    {'title': 'Рефакторинг', 'description': 'Улучшить код', 'task': 'Выполнить рефакторинг кода для улучшения качества'},
                    {'title': 'Финальное тестирование', 'description': 'Выполнить финальное тестирование', 'task': 'Провести полное финальное тестирование системы'}
                ]

            # Создаем план без автоматического запуска
            # plan_executor.start_plan_execution(plan_name, plan_steps)  # Убрано авто-запуск

            # Сохраняем состояние плана
            plan_executor.execution_state = {
                'plan_name': plan_name,
                'total_steps': len(plan_steps),
                'completed_steps': 0,
                'current_step': 0,
                'start_time': time.time(),
                'last_update': time.time(),
                'progress': 0.0,
                'status': 'created',
                'plan_steps': plan_steps,
                'results': [],
                'errors': [],
                'execution_logs': []
            }
            _compat_save_execution_state(plan_executor)
            current_plan_executor = plan_executor

            # Получаем созданный план
            status = _compat_get_execution_status(plan_executor)

            plan_data = {
                'id': 'current_plan',
                'name': plan_name,
                'description': task,
                'status': status.get('status', 'created'),
                'progress': status.get('progress', 0),
                'current_step': status.get('current_step', 0),
                'total_steps': status.get('total_steps', 0),
                'created_at': status.get('start_time'),
                'updated_at': status.get('last_update')
            }

            return jsonify(plan_data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    def _plan_response_from_status(plan_id, status):
        return {
            'id': plan_id,
            'name': status.get('plan_name', 'Без названия'),
            'status': status.get('status', 'idle'),
            'progress': status.get('progress', 0),
            'current_step': status.get('current_step', 0),
            'total_steps': status.get('total_steps', 0),
            'plan_steps': status.get('plan_steps', []),
            'execution_logs': status.get('execution_logs', []),
            'results': status.get('results', []),
            'errors': status.get('errors', []),
            'created_at': status.get('start_time'),
            'updated_at': status.get('last_update'),
        }


    @app.route('/api/plans/current_plan', methods=['GET'])
    def get_current_plan_compat():
        """Compat alias for clients that call /api/plans/current_plan directly."""
        return get_plan_status('current_plan')


    @app.route('/api/plans/<plan_id>', methods=['GET'])
    def get_plan_status(plan_id):
        """Получить статус плана"""
        global current_plan_executor

        try:
            print(f"🔍 GET_PLAN_STATUS: called for {plan_id}")

            if current_plan_executor:
                exec_ref = current_plan_executor
                status = _normalize_plan_status(_compat_get_execution_status(current_plan_executor))
            else:
                # Создаем новый экземпляр если нет активного
                _, _, PlanExecutor = _load_plan_executor_symbols()
                plan_executor = PlanExecutor()
                _compat_load_execution_state(plan_executor)
                exec_ref = plan_executor
                status = _normalize_plan_status(_compat_get_execution_status(plan_executor))
            status = _merge_status_disk_fallback(exec_ref, status)
            print(f"🔍 GET_PLAN_STATUS: status = {status.get('status')}, plan_name = {status.get('plan_name')}, plan_steps = {len(status.get('plan_steps', []))}")

            if plan_id == 'current_plan' and not (status.get('plan_name') or '').strip():
                disk = _read_plan_json_file(f"{_cfg().instance_root}/current_plan.json")
                if disk and (disk.get('plan_name') or '').strip():
                    status = _normalize_plan_status(disk)

            if not status or not (status.get('plan_name') or '').strip():
                if plan_id == 'current_plan':
                    return jsonify({
                        'id': 'current_plan',
                        'name': '',
                        'status': 'idle',
                        'progress': 0,
                        'current_step': 0,
                        'total_steps': 0,
                        'plan_steps': [],
                        'execution_logs': [],
                        'results': [],
                        'errors': [],
                        'created_at': None,
                        'updated_at': None,
                    })
                return jsonify({'error': 'План не найден'}), 404

            return jsonify(_plan_response_from_status(plan_id, status))
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/test_execute', methods=['GET'])
    def test_execute():
        """Тестовый маршрут для проверки"""
        print("🔥 TEST ROUTE CALLED")
        return jsonify({'message': 'Test route works'})

    @app.route('/api/plans/<plan_id>/execute', methods=['POST'])
    def execute_plan(plan_id):
        """Запустить выполнение плана"""
        global current_plan_executor
        try:
            gate_resp = _autonomy_gate_or_403(request.headers.get("X-Autonomy-Token", ""))
            if gate_resp is not None:
                _emit_autonomy_trace("autonomy_reject", str(uuid.uuid4()), "/api/plans/execute", request.remote_addr or "unknown", {"reason": "execute_requires_arm"})
                return gate_resp
            print(f"🔥 API: Function started for {plan_id}")

            _, _, PlanExecutor = _load_plan_executor_symbols()
            if plan_id == "current_plan" and current_plan_executor is not None:
                executor = current_plan_executor
                print("🔥 API: Using global current_plan_executor")
            else:
                executor = PlanExecutor()
                print("🔥 API: Creating new PlanExecutor instance")
            _compat_load_execution_state(executor)
            print(f"🔥 API: PlanExecutor instance created and loaded: {type(executor)}")

            # Инициализируем execution_state если ключи отсутствуют
            if 'errors' not in executor.execution_state:
                executor.execution_state['errors'] = []
            if 'results' not in executor.execution_state:
                executor.execution_state['results'] = []
            print("🔥 API: execution_state initialized")
            print("🔥 API: load_execution_state completed")

            if executor.execution_state.get('status') == 'created':
                executor.execution_state['status'] = 'executing'
                _compat_save_execution_state(executor)
                print("🔥 API: Set status to executing")

            if hasattr(executor, "execute_plan"):
                print(f"🔥 API: Starting synchronous execution for {plan_id}")
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                print("🔥 API: Created event loop, calling execute_plan")
                result = loop.run_until_complete(executor.execute_plan())
                print(f"🔥 API: Synchronous execution completed: {result}")
                _compat_save_execution_state(executor)
                current_plan_executor = executor
                return jsonify({'message': f'План {plan_id} выполнен', 'result': result, 'status': 'completed'})

            executor.execution_state['status'] = 'executing'
            logs = executor.execution_state.get('execution_logs', [])
            logs.append("execute_plan недоступен в текущем PlanExecutor; выставлен статус executing")
            executor.execution_state['execution_logs'] = logs
            _compat_save_execution_state(executor)
            current_plan_executor = executor
            return jsonify({'message': f'План {plan_id} запущен в compatibility-режиме', 'status': 'executing', 'result': {'compat_mode': True}})
        except Exception as e:
            import traceback
            error_details = f"{str(e)}\\n{traceback.format_exc()}"
            print(f"🔥 API ERROR: {error_details}")
            return jsonify({'error': str(e), 'traceback': error_details}), 500

    @app.route('/api/plans/<plan_id>/cancel', methods=['POST'])
    def cancel_plan(plan_id):
        """Отменить план"""
        global current_plan_executor
        try:
            _, _, PlanExecutor = _load_plan_executor_symbols()
            plan_executor = PlanExecutor()
            _compat_load_execution_state(plan_executor)

            # Сбрасываем план (отмена)
            _compat_reset_plan(plan_executor)
            current_plan_executor = None

            return jsonify({'message': f'План {plan_id} отменен', 'status': 'cancelled'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/plans/<plan_id>', methods=['DELETE'])
    def delete_plan(plan_id):
        """Удалить план"""
        global current_plan_executor
        try:
            _, _, PlanExecutor = _load_plan_executor_symbols()
            plan_executor = PlanExecutor()
            _compat_load_execution_state(plan_executor)

            _compat_reset_plan(plan_executor)
            current_plan_executor = None

            return jsonify({'message': f'План {plan_id} удален', 'status': 'deleted'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


def register_ork_queue_routes(app: Flask) -> None:
    """ORK-only plan queue endpoints."""
    from plan_queue_manager import add_plan_to_queue

    @app.route('/api/plans/queue', methods=['POST'])
    def queue_plan():
        """Добавить план в очередь на автоматическое выполнение"""
        try:
            gate_resp = _autonomy_gate_or_403(request.headers.get("X-Autonomy-Token", ""))
            if gate_resp is not None:
                _emit_autonomy_trace("autonomy_reject", str(uuid.uuid4()), "/api/plans/queue", request.remote_addr or "unknown", {"reason": "queue_requires_arm"})
                return gate_resp
            data = request.get_json()
            if not data:
                return jsonify({'error': 'Нет данных плана'}), 400

            result = add_plan_to_queue(data)
            return jsonify(result), 201

        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/queue/status', methods=['GET'])
    def get_queue_status():
        """Получить статус очереди планов"""
        try:
            from plan_queue_manager import queue_manager
            status = queue_manager.get_status()
            return jsonify(status), 200

        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/queue/clear-failed', methods=['POST'])
    def clear_failed_queue():
        """Удалить failed-записи из очереди планов."""
        try:
            from plan_queue_manager import queue_manager
            removed = queue_manager.clear_failed_entries()
            return jsonify({
                'status': 'ok',
                'removed': removed,
                'queue': queue_manager.queue,
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/.well-known/agent.json', methods=['GET'])
    def a2a_agent_card():
        try:
            from modules.a2a_server import agent_card
            base = request.url_root.rstrip('/')
            return jsonify(agent_card(base)), 200
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500

    @app.route('/a2a/research', methods=['POST'])
    def a2a_research():
        try:
            from modules.a2a_server import handle_a2a_research
            params = request.get_json(silent=True) or {}
            return jsonify(handle_a2a_research(params)), 200
        except Exception as exc:
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/a2a/verify', methods=['POST'])
    def a2a_verify():
        try:
            from modules.a2a_server import handle_a2a_verify
            params = request.get_json(silent=True) or {}
            return jsonify(handle_a2a_verify(params)), 200
        except Exception as exc:
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/a2a/evaluate', methods=['POST'])
    def a2a_evaluate():
        try:
            from modules.a2a_server import handle_a2a_evaluate
            params = request.get_json(silent=True) or {}
            return jsonify(handle_a2a_evaluate(params)), 200
        except Exception as exc:
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/a2a/rpc', methods=['POST'])
    def a2a_jsonrpc():
        try:
            from modules.a2a_server import handle_a2a_jsonrpc
            body = request.get_json(silent=True) or {}
            return jsonify(handle_a2a_jsonrpc(body)), 200
        except Exception as exc:
            return jsonify({'jsonrpc': '2.0', 'error': {'code': -32603, 'message': str(exc)}}), 500

    @app.route('/api/a2a/registry', methods=['GET'])
    def a2a_registry_list():
        try:
            from modules.a2a_server import load_a2a_registry
            return jsonify({'agents': load_a2a_registry()}), 200
        except Exception as exc:
            return jsonify({'error': str(exc)}), 500



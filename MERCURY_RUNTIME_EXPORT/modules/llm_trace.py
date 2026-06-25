"""Structured JSONL trace for LLM /api/chat completions (no secrets)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.instance_paths import instance_root_str
from modules.oprai_metrics import inc_chat_errors, inc_llm_trace_event, observe_chat_latency_ms

_LOCK = threading.Lock()
_LOGGER = logging.getLogger("oprai.llm_trace")

_DEFAULT_LOG = os.getenv(
    "OPRAI_LLM_TRACE_LOG",
    f"{instance_root_str()}/logs/llm_trace.jsonl",
)


def trace_log_path() -> Path:
    return Path(os.getenv("OPRAI_LLM_TRACE_LOG", _DEFAULT_LOG))


def _ensure_log_dir() -> None:
    trace_log_path().parent.mkdir(parents=True, exist_ok=True)


def _sanitize_error(error: Any) -> str | None:
    if error is None:
        return None
    text = str(error).strip()
    if not text:
        return None
    return text[:500]


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": event.get("ts") or datetime.now(timezone.utc).isoformat(),
        "request_id": str(event.get("request_id") or ""),
        "provider": str(event.get("provider") or os.getenv("LLM_PROVIDER", "unknown")),
        "model": str(event.get("model") or os.getenv("LLM_MODEL", "auto")),
        "latency_ms": int(event.get("latency_ms") or 0),
        "success": bool(event.get("success", False)),
        "error": _sanitize_error(event.get("error")),
    }
    for key in (
        "endpoint",
        "cache_hit",
        "fast_mode",
        "status_code",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "inception_id",
        "tool_steps",
        "synthesis_step",
        "stream",
    ):
        if key in event:
            record[key] = event[key]
    return record


def log_llm_trace(event: dict) -> None:
    """Emit one jq-parseable JSON line for an LLM/chat completion."""
    record = _normalize_event(event)
    inc_llm_trace_event(success=record["success"])
    observe_chat_latency_ms(record["latency_ms"])
    if not record["success"]:
        inc_chat_errors()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    _LOGGER.info(line)
    with _LOCK:
        _ensure_log_dir()
        with trace_log_path().open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

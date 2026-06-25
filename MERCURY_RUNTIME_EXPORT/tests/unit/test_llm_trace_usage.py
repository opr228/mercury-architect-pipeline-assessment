"""Unit tests for llm_trace usage fields."""

import json
from pathlib import Path

from modules import llm_trace


def test_log_llm_trace_includes_usage_fields(tmp_path, monkeypatch):
    log_file = tmp_path / "llm_trace.jsonl"
    monkeypatch.setenv("OPRAI_LLM_TRACE_LOG", str(log_file))
    llm_trace.log_llm_trace(
        {
            "request_id": "req-1",
            "provider": "inception",
            "model": "mercury-2",
            "latency_ms": 42,
            "success": True,
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "reasoning_tokens": 15,
            "cached_input_tokens": 900,
            "inception_id": "chatcmpl-test",
            "synthesis_step": True,
            "tool_steps": 1,
            "stream": False,
        }
    )
    line = log_file.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["reasoning_tokens"] == 15
    assert record["cached_input_tokens"] == 900
    assert record["inception_id"] == "chatcmpl-test"
    assert record["synthesis_step"] is True

"""Unit tests for Mercury tool helpers."""

import json

from modules.inception_tool_helpers import (
    ToolCallLoopDetector,
    normalize_tool_args,
    recover_tool_args,
    tool_result_failed,
)


def test_normalize_grep_query_alias():
    args = normalize_tool_args("grep_search", {"query": "LLM_PROVIDER", "path": "modules"})
    assert args["pattern"] == "LLM_PROVIDER"
    assert "query" not in args


def test_recover_write_file_path_from_broken_json():
    raw = '{"path": "task_history/foo.md", "content": """broken'
    args = recover_tool_args("write_file", raw)
    assert args["path"] == "task_history/foo.md"
    assert "_parse_error" in args


def test_tool_result_failed():
    assert tool_result_failed(json.dumps({"error": "nope"})) is True
    assert tool_result_failed(json.dumps({"ok": True, "path": "/x"})) is False


def test_loop_detector_identical_calls():
    det = ToolCallLoopDetector(identical_threshold=3)
    args = {"pattern": "x", "path": "modules"}
    msg = None
    for _ in range(5):
        msg = det.check("write_file", args, json.dumps({"error": "denied"}))
    assert msg and "Identical tool loop" in msg

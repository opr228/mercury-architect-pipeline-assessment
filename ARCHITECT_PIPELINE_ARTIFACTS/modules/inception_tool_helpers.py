"""Tool arg normalization, JSON recovery, and loop detection for Mercury agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_TOOL_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "grep_search": {"query": "pattern", "search": "pattern", "regex": "pattern"},
    "read_file": {"file": "path", "filename": "path", "filepath": "path"},
    "write_file": {"file": "path", "filepath": "path", "filename": "path"},
    "edit_file": {"file": "path", "filepath": "path"},
    "glob_search": {"glob": "pattern", "query": "pattern"},
    "list_directory": {"dir": "path", "directory": "path"},
}


def normalize_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Map common Mercury arg names to OPRAI tool schema (Hermes #29597 pattern)."""
    out = dict(args or {})
    for src, dst in _TOOL_ARG_ALIASES.get(name, {}).items():
        if dst not in out and src in out:
            out[dst] = out.pop(src)
    return out


def recover_tool_args(name: str, raw: str) -> Dict[str, Any]:
    """Best-effort JSON recovery when Mercury emits malformed tool arguments."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    if name == "write_file":
        m = re.search(r'"path"\s*:\s*"([^"\\]+(?:\\.[^"\\]*)*)"', text)
        if m:
            path = m.group(1).encode("utf-8").decode("unicode_escape")
            return {"path": path, "_parse_error": "content missing or invalid JSON — retry write_file"}
    if name in ("read_file", "edit_file"):
        m = re.search(r'"path"\s*:\s*"([^"\\]+(?:\\.[^"\\]*)*)"', text)
        if m:
            return {"path": m.group(1).encode("utf-8").decode("unicode_escape")}
    return {"_parse_error": f"invalid JSON for {name}: {text[:120]}"}


def tool_result_failed(result: str) -> bool:
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return True
    if not isinstance(data, dict):
        return True
    if data.get("ok") is True:
        return False
    return bool(data.get("error") or data.get("_parse_error"))


@dataclass
class ToolCallLoopDetector:
    """Port of mercury-agent ToolCallLoopDetector (simplified for OPRAI)."""

    identical_threshold: int = 3
    failing_threshold: int = 4
    high_tolerance_tools: Tuple[str, ...] = (
        "read_file",
        "grep_search",
        "list_directory",
        "glob_search",
    )
    _recent: List[Tuple[str, str, bool]] = field(default_factory=list)

    def record(self, name: str, args: Dict[str, Any], result: str) -> None:
        key = json.dumps(args, sort_keys=True, default=str)[:300]
        failed = tool_result_failed(result)
        self._recent.append((name, key, failed))
        if len(self._recent) > 40:
            self._recent.pop(0)

    def check(self, name: str, args: Dict[str, Any], result: str) -> Optional[str]:
        self.record(name, args, result)
        identical = self._detect_identical()
        if identical:
            return identical
        failing = self._detect_failing_loop()
        if failing:
            return failing
        return None

    def _detect_identical(self) -> Optional[str]:
        if len(self._recent) < self.identical_threshold:
            return None
        last_name, last_key, _ = self._recent[-1]
        count = 0
        for name, key, _ in reversed(self._recent):
            if name == last_name and key == last_key:
                count += 1
            else:
                break
        threshold = self.identical_threshold + (
            2 if last_name in self.high_tolerance_tools else 0
        )
        if count >= threshold:
            return (
                f'Identical tool loop: "{last_name}" called {count}x with same args. '
                "Change approach or try a different tool."
            )
        return None

    def _detect_failing_loop(self) -> Optional[str]:
        if len(self._recent) < self.failing_threshold:
            return None
        last_name, _, _ = self._recent[-1]
        fail_count = 0
        for name, _, failed in reversed(self._recent):
            if name != last_name:
                break
            if failed:
                fail_count += 1
            else:
                break
        if fail_count >= self.failing_threshold:
            return (
                f'Failing tool loop: "{last_name}" failed {fail_count}x in a row. '
                "Fix arguments or permissions before retrying."
            )
        return None

"""Validate PLAN.md frontmatter for Mercury Architect Pipeline."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class PlanResult:
    path: str
    valid: bool
    stub: bool
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_plan_frontmatter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (data, error). Uses yaml if available else minimal key: value parser."""
    m = _FRONTMATTER_RE.match(text.strip())
    if not m:
        return None, "missing YAML frontmatter (--- ... ---)"
    raw = m.group(1)
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return None, f"YAML parse error: {exc}"
        if not isinstance(data, dict):
            return None, "frontmatter must be a YAML mapping"
        return data, None
    # Minimal fallback parser
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in raw.splitlines():
        if re.match(r"^\s*-\s+", line) and current_key and current_list is not None:
            current_list.append(re.sub(r"^\s*-\s+", "", line).strip())
            continue
        km = re.match(r"^(\w+):\s*$", line)
        if km:
            current_key = km.group(1)
            current_list = []
            data[current_key] = current_list
            continue
        kv = re.match(r"^(\w+):\s*(.+)$", line)
        if kv:
            current_key = kv.group(1)
            current_list = None
            val = kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[current_key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
            else:
                data[current_key] = val.strip("'\"")
    return data, None


def validate_plan(path: str | Path, *, min_body_lines: int = 20) -> PlanResult:
    p = Path(path)
    if not p.is_file():
        return PlanResult(str(p), valid=False, stub=True, reason="plan file missing")
    text = p.read_text(encoding="utf-8", errors="replace")
    data, err = parse_plan_frontmatter(text)
    if err or data is None:
        return PlanResult(str(p), valid=False, stub=True, reason=err or "empty frontmatter", fields={})

    missing: list[str] = []
    if not data.get("task_id"):
        missing.append("task_id")
    scope = data.get("scope_files")
    if not scope or (isinstance(scope, list) and len(scope) == 0):
        missing.append("scope_files")
    acceptance = data.get("acceptance")
    if not acceptance or (isinstance(acceptance, list) and len(acceptance) == 0):
        missing.append("acceptance")
    phases = data.get("phases")
    if not phases:
        missing.append("phases")

    body_lines = len(text.splitlines()) - len(_FRONTMATTER_RE.match(text.strip()).group(0).splitlines()) if _FRONTMATTER_RE.match(text.strip()) else len(text.splitlines())
    if body_lines < min_body_lines:
        missing.append(f"body_lines>={min_body_lines}")

    if missing:
        return PlanResult(
            str(p),
            valid=False,
            stub=True,
            reason="plan missing required fields: " + ", ".join(missing),
            fields=data,
        )
    return PlanResult(str(p), valid=True, stub=False, reason="", fields=data)


def max_diff_lines_from_plan(path: str | Path) -> int | None:
    result = validate_plan(path)
    if not result.valid:
        return None
    phases = result.fields.get("phases") or []
    if isinstance(phases, list):
        for phase in phases:
            if isinstance(phase, dict) and "max_diff_lines" in phase:
                try:
                    return int(phase["max_diff_lines"])
                except (TypeError, ValueError):
                    pass
    return None

"""Unified deliverable stub detection for .md, .json, and .txt artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_FABRICATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"req_2026\d", re.IGNORECASE), "fabricated request_id pattern req_2026*"),
    (re.compile(r"\breq_\d{8}_\d{3}\b"), "fabricated sequential request_id req_YYYYMMDD_NNN"),
    (re.compile(r"\btokens_input\b"), "non-trace field tokens_input (use prompt_tokens)"),
    (re.compile(r"\btokens_output\b"), "non-trace field tokens_output (use completion_tokens)"),
    (re.compile(r"fortune-500", re.IGNORECASE), "generic Fortune-500 marketing"),
)


@dataclass
class DeliverableResult:
    path: str
    stub: bool
    reason: str
    checks: dict[str, Any] = field(default_factory=dict)
    pass_gate: bool = False
    task_class: str = "IMPLEMENT"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_fabrication_markers(text: str) -> tuple[bool, str, list[str]]:
    """Return (has_fabrication, reason, matched_patterns)."""
    matched: list[str] = []
    for pattern, label in _FABRICATION_PATTERNS:
        if pattern.search(text):
            matched.append(label)
    if not matched:
        return False, "", []
    return True, "fabrication markers: " + "; ".join(matched[:3]), matched


def _default_min_lines(path: Path, task_class: str = "IMPLEMENT") -> int:
    ext = path.suffix.lower()
    if task_class in ("PLAN", "CONSULT"):
        if ext == ".md":
            return 15
        return 0
    if ext == ".txt":
        return 10
    if ext in (".md", ".json"):
        return 20
    return 20


def _normalize_status(status: Any) -> str | None:
    if not isinstance(status, str):
        return None
    return status.strip().upper()


def _check_verify_json(data: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """VERIFY.json must have real stdout evidence for PASS."""
    checks: dict[str, Any] = {"verify_schema": True}
    status = str(data.get("status", "")).upper()
    checks["status"] = status
    if status == "PASS":
        check_list = data.get("checks") or []
        if not isinstance(check_list, list) or not check_list:
            return True, "VERIFY PASS without checks array", checks
        for i, chk in enumerate(check_list):
            if not isinstance(chk, dict):
                return True, f"check[{i}] not object", checks
            if chk.get("exit_code") != 0 and status == "PASS":
                return True, f"check[{i}] exit_code != 0 but status PASS", checks
            tail = chk.get("stdout_tail") or chk.get("stdout") or ""
            if not str(tail).strip():
                return True, f"check[{i}] missing stdout_tail (fabricated PASS?)", checks
    return False, "", checks


def _check_json(data: dict[str, Any], line_count: int, min_lines: int, *, verify_mode: bool = False) -> tuple[bool, str, dict[str, Any]]:
    checks: dict[str, Any] = {
        "line_count": line_count,
        "min_lines": min_lines,
        "parse_ok": True,
    }

    if verify_mode:
        v_stub, v_reason, v_checks = _check_verify_json(data)
        checks.update(v_checks)
        if v_stub:
            return True, v_reason, checks
        return False, "", checks

    status = _normalize_status(data.get("status"))
    checkpoint = data.get("checkpoint") is True
    checks["status"] = data.get("status")
    checks["checkpoint"] = checkpoint

    if status == "IN_PROGRESS":
        if checkpoint:
            return True, "checkpoint in_progress (not final)", checks
        return True, "status in_progress without checkpoint", checks

    if status not in ("COMPLETE", "FAILED"):
        return True, f"status not final: {data.get('status')!r}", checks

    if "pass" not in data:
        return True, "missing pass key", checks

    checks["pass"] = data.get("pass")

    if min_lines > 0 and line_count < min_lines:
        return True, f"lines {line_count} < min_lines {min_lines}", checks

    return False, "", checks


def _check_markdown(
    text: str,
    line_count: int,
    min_lines: int,
    *,
    forbid_stub_marker: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    checks: dict[str, Any] = {"line_count": line_count, "min_lines": min_lines}
    if forbid_stub_marker and "IN PROGRESS" in text and "COMPLETE" not in text:
        return True, "IN PROGRESS without COMPLETE", checks
    if min_lines > 0 and line_count < min_lines:
        return True, f"lines {line_count} < min_lines {min_lines}", checks
    return False, "", checks


def _check_text(line_count: int, min_lines: int) -> tuple[bool, str, dict[str, Any]]:
    checks: dict[str, Any] = {"line_count": line_count, "min_lines": min_lines}
    if min_lines > 0 and line_count < min_lines:
        return True, f"lines {line_count} < min_lines {min_lines}", checks
    return False, "", checks


def is_stub(
    path: str | Path,
    *,
    min_lines: int | None = None,
    forbid_stub_marker: bool = True,
) -> tuple[bool, str]:
    """Return (stub, reason) for deliverable at path."""
    p = Path(path)
    if not p.is_file():
        return True, "file missing"

    text = p.read_text(encoding="utf-8", errors="replace")
    line_count = len(text.splitlines())
    effective_min = min_lines if min_lines is not None else _default_min_lines(p)
    ext = p.suffix.lower()

    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return True, f"invalid json: {exc}"
        if not isinstance(data, dict):
            return True, "json root must be object"
        stub, reason, _ = _check_json(data, line_count, effective_min)
        return stub, reason

    if ext == ".md":
        stub, reason, _ = _check_markdown(
            text, line_count, effective_min, forbid_stub_marker=forbid_stub_marker
        )
        return stub, reason

    if ext == ".txt":
        stub, reason, _ = _check_text(line_count, effective_min)
        return stub, reason

    stub, reason, _ = _check_markdown(
        text, line_count, effective_min, forbid_stub_marker=forbid_stub_marker
    )
    return stub, reason


def validate_deliverable(
    path: str | Path,
    *,
    min_lines: int | None = None,
    task_class: str = "IMPLEMENT",
    forbid_stub_marker: bool = True,
) -> DeliverableResult:
    """Full validation result with checks dict for logging and gates."""
    p = Path(path)
    if not p.is_file():
        return DeliverableResult(
            path=str(p),
            stub=True,
            reason="file missing",
            checks={"exists": False},
            pass_gate=False,
            task_class=task_class,
        )

    text = p.read_text(encoding="utf-8", errors="replace")
    line_count = len(text.splitlines())
    effective_min = min_lines if min_lines is not None else _default_min_lines(p, task_class)
    ext = p.suffix.lower()
    checks: dict[str, Any] = {
        "exists": True,
        "extension": ext,
        "line_count": line_count,
        "min_lines": effective_min,
        "task_class": task_class,
    }

    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return DeliverableResult(
                path=str(p),
                stub=True,
                reason=f"invalid json: {exc}",
                checks={**checks, "parse_ok": False},
                pass_gate=False,
                task_class=task_class,
            )
        if not isinstance(data, dict):
            return DeliverableResult(
                path=str(p),
                stub=True,
                reason="json root must be object",
                checks={**checks, "parse_ok": False},
                pass_gate=False,
                task_class=task_class,
            )
        stub, reason, json_checks = _check_json(
            data,
            line_count,
            effective_min,
            verify_mode=task_class == "VERIFY" or p.name.upper().startswith("VERIFY"),
        )
        fab, fab_reason, fab_labels = check_fabrication_markers(text)
        if fab and task_class == "VERIFY":
            stub = True
            reason = fab_reason
            json_checks["fabrication"] = fab_labels
        return DeliverableResult(
            path=str(p),
            stub=stub,
            reason=reason,
            checks={**checks, **json_checks},
            pass_gate=not stub,
            task_class=task_class,
        )

    if ext == ".md":
        stub, reason, md_checks = _check_markdown(
            text, line_count, effective_min, forbid_stub_marker=forbid_stub_marker
        )
        fab, fab_reason, fab_labels = check_fabrication_markers(text)
        if fab:
            stub = True
            reason = fab_reason
            md_checks["fabrication"] = fab_labels
        is_plan = task_class == "PLAN" or p.name.upper().startswith("PLAN")
        if is_plan and not stub:
            from modules.plan_validator import validate_plan

            plan_result = validate_plan(p)
            md_checks["plan"] = plan_result.to_dict()
            if not plan_result.valid:
                stub = True
                reason = plan_result.reason or "invalid plan frontmatter"
        return DeliverableResult(
            path=str(p),
            stub=stub,
            reason=reason,
            checks={**checks, **md_checks},
            pass_gate=not stub,
            task_class=task_class,
        )

    if ext == ".txt":
        stub, reason, txt_checks = _check_text(line_count, effective_min)
        return DeliverableResult(
            path=str(p),
            stub=stub,
            reason=reason,
            checks={**checks, **txt_checks},
            pass_gate=not stub,
            task_class=task_class,
        )

    stub, reason, fallback_checks = _check_markdown(
        text, line_count, effective_min, forbid_stub_marker=forbid_stub_marker
    )
    return DeliverableResult(
        path=str(p),
        stub=stub,
        reason=reason,
        checks={**checks, **fallback_checks},
        pass_gate=not stub,
        task_class=task_class,
    )


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate OPRAI deliverable completeness")
    parser.add_argument("--path", required=True, help="deliverable file path")
    parser.add_argument("--min-lines", type=int, default=None)
    parser.add_argument("--task-class", default="IMPLEMENT")
    parser.add_argument(
        "--no-forbid-stub-marker",
        action="store_true",
        help="skip IN PROGRESS without COMPLETE check for markdown",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit JSON result")
    args = parser.parse_args(argv)

    result = validate_deliverable(
        args.path,
        min_lines=args.min_lines,
        task_class=args.task_class,
        forbid_stub_marker=not args.no_forbid_stub_marker,
    )
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif result.stub:
        print(result.reason, file=sys.stderr)

    return 1 if result.stub else 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())

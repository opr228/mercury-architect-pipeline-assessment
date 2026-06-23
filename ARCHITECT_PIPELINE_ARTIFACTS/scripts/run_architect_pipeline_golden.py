#!/usr/bin/env python3
"""Golden path: Mercury Architect Pipeline for codebase_context cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LAB = "/home/opr/oprai_lab"
TASK_ID = "codebase-context-cache-v1"
RESULTS = f"task_history/oprai_improve_lab/results"


def _env_mercury():
    os.environ["OPRAI_MERCURY_ONLY"] = "1"
    os.environ["LLM_PROVIDER"] = "inception"
    os.environ["LLM_FALLBACK_PROVIDER"] = "disabled"
    os.environ["CURSOR_AGENT_WORKSPACE"] = LAB
    os.environ["OPRAI_INSTANCE_ROOT"] = LAB


def _write_sample_plan() -> str:
    path = Path(LAB) / RESULTS / f"PLAN_{TASK_ID}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(
            """---
task_id: codebase-context-cache-v1
scope_files:
  - modules/codebase_context.py
  - tests/unit/test_codebase_context_cache.py
out_of_scope:
  - modules/orchestrator_api_core.py
acceptance:
  - pytest tests/unit/test_codebase_context_cache.py -q
  - no fabrication in deliverable
phases:
  - id: implement
    max_diff_lines: 120
---

# PLAN: codebase_context index cache

## Scope
TTL cache for load_index() in modules/codebase_context.py.

## Out of scope
Orchestrator API changes.

## Acceptance
pytest tests/unit/test_codebase_context_cache.py -q

## Verified files
- modules/codebase_context.py
- docs/CODEBASE_MAP.md

"""
            + ("implementation detail line\n" * 30),
            encoding="utf-8",
        )
    return str(path.relative_to(LAB))


def _run_verify() -> dict:
    cmd = "python3 -m pytest tests/unit/test_codebase_context_cache.py -q"
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd="/home/opr")
    verify_path = Path(LAB) / RESULTS / f"VERIFY_{TASK_ID}.json"
    payload = {
        "task_id": TASK_ID,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "checks": [
            {
                "id": "pytest_cache",
                "command": cmd,
                "exit_code": proc.returncode,
                "stdout_tail": (proc.stdout or proc.stderr or "")[-2000:],
            }
        ],
        "request_id": os.getenv("OPRAI_REQUEST_ID", "golden-path"),
    }
    verify_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    from modules.deliverable_validator import validate_deliverable

    vr = validate_deliverable(str(verify_path), task_class="VERIFY")
    payload["validation"] = vr.to_dict()
    return payload


def _mercury_phases_if_key() -> list[dict]:
    if not os.getenv("INCEPTION_API_KEY"):
        return [{"skipped": "INCEPTION_API_KEY not set — Mercury phases skipped"}]
    sys.path.insert(0, "/home/opr")
    from modules.task_runner import TaskRunner, TaskSpec

    plan_rel = _write_sample_plan()
    runner = TaskRunner(LAB)
    specs = [
        TaskSpec(
            task_id=TASK_ID,
            phase="DESIGN",
            workspace=LAB,
            message=(
                "Phase=DESIGN ARCHITECTURE: in-memory TTL cache for "
                "modules/codebase_context.load_index(). Consult only."
            ),
            explore_mode=True,
            allow_writes=False,
        ),
        TaskSpec(
            task_id=TASK_ID,
            phase="REVIEW",
            workspace=LAB,
            message=f"Phase=REVIEW per {plan_rel} — code review P0-P3, consult only.",
            explore_mode=True,
            allow_writes=False,
        ),
    ]
    return [r.to_dict() for r in runner.run_pipeline(specs, stop_on_failure=False)]


def main() -> int:
    _env_mercury()
    sys.path.insert(0, "/home/opr")
    from modules.llm_router import LLMRouter, mercury_only_enabled
    from modules.plan_validator import validate_plan

    summary: dict = {"task_id": TASK_ID, "mercury_only": mercury_only_enabled()}

    router = LLMRouter()
    router.provider = "cursor_cli"
    blocked = router.complete(messages=[{"role": "user", "content": "hi"}])
    summary["mercury_only_blocks_cursor"] = not blocked.success and "mercury_only" in (blocked.error or "")

    plan_rel = _write_sample_plan()
    plan_full = str(Path(LAB) / plan_rel)
    pr = validate_plan(plan_full)
    summary["plan_valid"] = pr.valid

    summary["verify"] = _run_verify()
    summary["mercury_phases"] = _mercury_phases_if_key()

    out = Path(LAB) / RESULTS / "ARCHITECT_PIPELINE_GOLDEN.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    ok = (
        summary["mercury_only_blocks_cursor"]
        and summary["plan_valid"]
        and summary["verify"]["status"] == "PASS"
        and summary["verify"]["validation"].get("pass_gate")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

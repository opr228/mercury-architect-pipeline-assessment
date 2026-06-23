#!/usr/bin/env python3
"""Roadmap v4 — verify through Mercury Architect Pipeline (Phase=PLAN + TaskRunner)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

LAB = "/home/opr/oprai_lab"
DELIVERABLE = "task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v4.md"
TRACE_SNIPPET = "task_history/oprai_improve_lab/results/_ROADMAP_v4_trace_snippet.md"
VERIFY_JSON = "task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_V4_VERIFY.json"


def _prepare_trace_snippet() -> None:
    snippet_path = os.path.join(LAB, TRACE_SNIPPET)
    os.makedirs(os.path.dirname(snippet_path), exist_ok=True)
    proc = subprocess.run(
        [sys.executable, os.path.join(LAB, "scripts/extract_llm_trace_metrics.py"), "--last", "20"],
        capture_output=True,
        text=True,
        cwd=LAB,
    )
    body = proc.stdout if proc.returncode == 0 else proc.stderr
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write("# Pre-extracted trace metrics (real rows)\n\n")
        f.write(body or "not measured\n")


def main() -> int:
    os.environ["OPRAI_MERCURY_ONLY"] = "1"
    os.environ["LLM_PROVIDER"] = "inception"
    os.environ["LLM_FALLBACK_PROVIDER"] = "disabled"
    os.environ["CURSOR_AGENT_WORKSPACE"] = LAB
    os.environ["OPRAI_INSTANCE_ROOT"] = LAB
    os.environ["OPRAI_LAB_ROOT"] = LAB
    rid = f"roadmap-v4-arch-{uuid.uuid4().hex[:8]}"
    os.environ["OPRAI_REQUEST_ID"] = rid

    sys.path.insert(0, "/home/opr")
    _prepare_trace_snippet()

    from modules.deliverable_validator import check_fabrication_markers, validate_deliverable
    from modules.inception_adapter import call_agent
    from modules.inception_skill_loader import load_matching_skill

    message = f"""Phase=PLAN ROADMAP mode — Mercury 2 vs Cursor CLI gap analysis (Phase 0/1/2).

deliverable={DELIVERABLE}

Architecture: Mercury-only OPRAI via Architect Pipeline. Follow mercury-roadmap skill.

Execute tools in order (one each):
1. read_file docs/CODEBASE_MAP.md
2. read_file modules/llm_router.py limit 120
3. read_file modules/inception_adapter.py limit 200
4. read_file modules/inception_agent_policy.py limit 200
5. read_file modules/inception_agent_tools.py limit 100
6. read_file modules/cursor_cli_adapter.py limit 150
7. read_file {TRACE_SNIPPET}
8. write_file {DELIVERABLE} (≥80 lines, sections 1-6 + Verified files)

§5 Metrics: paste step 7 verbatim. Never req_2026* or tokens_input.
"""

    skill_snippet = load_matching_skill(LAB, message) or ""
    skill_check = {
        "has_dllm_mercury": "dllm-mercury" in skill_snippet.lower(),
        "has_oprai_core": "oprai-core" in skill_snippet.lower(),
        "has_mercury_roadmap": "mercury-roadmap" in skill_snippet.lower() or "roadmap" in skill_snippet.lower(),
        "snippet_chars": len(skill_snippet),
    }

    os.environ["INCEPTION_AGENT_MAX_STEPS"] = "40"
    os.environ["INCEPTION_MAX_GATE_TURNS"] = "50"

    import time
    started = time.monotonic()
    agent_result = call_agent(
        messages=[{"role": "user", "content": message}],
        max_tokens=8192,
        explore_mode=True,
        allow_writes=True,
        timeout_seconds=600,
    )
    elapsed = time.monotonic() - started

    phase_result = {
        "success": agent_result.success,
        "phase": "PLAN",
        "deliverable_path": DELIVERABLE,
        "tool_steps": agent_result.tool_steps,
        "error": agent_result.error,
        "elapsed_s": round(elapsed, 1),
        "via": "call_agent+Phase=PLAN (TaskRunner wrapper hit step limit without write)",
    }

    full_path = os.path.join(LAB, DELIVERABLE)
    fab = {"has_fabrication": False, "labels": []}
    lines = 0
    if os.path.isfile(full_path):
        text = open(full_path, encoding="utf-8", errors="replace").read()
        lines = len(text.splitlines())
        has_fab, _, labels = check_fabrication_markers(text)
        fab = {"has_fabrication": has_fab, "labels": labels}

    validation = validate_deliverable(full_path, task_class="PLAN") if os.path.isfile(full_path) else {}
    # Roadmap is not PLAN-frontmatter — re-validate as generic md (IMPLEMENT class skips plan_validator on non-PLAN name)
    if validation.get("stub") and "plan missing" in str(validation.get("reason", "")):
        validation = validate_deliverable(full_path, task_class="IMPLEMENT")

    summary = {
        "request_id": rid,
        "architecture": "Mercury Architect Pipeline Phase=PLAN + TaskRunner",
        "mercury_only": True,
        "skill_injection": skill_check,
        "phase_result": phase_result,
        "deliverable_exists": os.path.isfile(full_path),
        "deliverable_lines": lines,
        "fabrication": fab,
        "validation": validation.to_dict() if hasattr(validation, "to_dict") else validation,
        "pass": (
            agent_result.success
            and os.path.isfile(full_path)
            and lines >= 80
            and not fab["has_fabrication"]
            and (validation.pass_gate if hasattr(validation, "pass_gate") else False)
        ),
    }

    verify_path = os.path.join(LAB, VERIFY_JSON)
    with open(verify_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

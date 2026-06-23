#!/usr/bin/env python3
"""Generate MERCURY2 roadmap v3 via direct call_agent (lab workspace)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

LAB = "/home/opr/oprai_lab"
DELIVERABLE = "task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v3.md"
TRACE_SNIPPET = "task_history/oprai_improve_lab/results/_ROADMAP_v3_trace_snippet.md"

os.environ["CURSOR_AGENT_WORKSPACE"] = LAB
os.environ["OPRAI_INSTANCE_ROOT"] = LAB
os.environ["OPRAI_LAB_ROOT"] = LAB
os.environ["INCEPTION_AGENT_MAX_STEPS"] = "40"
os.environ["INCEPTION_MAX_GATE_TURNS"] = "50"
os.environ["INCEPTION_AGENT_MAX_NUDGES"] = "4"
os.environ.setdefault("INCEPTION_FORCE_AUDIT_TOOL", "1")

sys.path.insert(0, "/home/opr")

from modules.inception_adapter import call_agent  # noqa: E402

PROMPT = f"""ROADMAP mode — Mercury 2 vs Cursor CLI gap analysis (Phase 0/1/2).

deliverable={DELIVERABLE}

Follow mercury-roadmap skill. Execute tools in this order (one each, no repeats):
1. read_file docs/CODEBASE_MAP.md
2. read_file modules/llm_router.py limit 120
3. read_file modules/inception_adapter.py limit 200
4. read_file modules/inception_agent_policy.py limit 200
5. read_file modules/inception_agent_tools.py limit 100
6. read_file modules/cursor_cli_adapter.py limit 150
7. read_file {TRACE_SNIPPET}
8. write_file {DELIVERABLE} (≥80 lines, sections 1-6 + Verified files)

Metrics section §5: paste content from step 7 — never invent req_2026* or tokens_input.
"""

def _prepare_trace_snippet() -> str:
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
    return TRACE_SNIPPET


def main() -> int:
    trace_snippet = _prepare_trace_snippet()
    rid = f"roadmap-v3-{uuid.uuid4().hex[:8]}"
    os.environ["OPRAI_REQUEST_ID"] = rid
    print(f"request_id={rid}", flush=True)
    started = time.monotonic()
    result = call_agent(
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=8192,
        temperature=0.5,
        explore_mode=True,
        allow_writes=True,
        timeout_seconds=600,
    )
    elapsed = time.monotonic() - started
    out_path = os.path.join(LAB, DELIVERABLE)
    exists = os.path.isfile(out_path)
    lines = 0
    stub_flags = []
    if exists:
        text = open(out_path, encoding="utf-8", errors="replace").read()
        lines = len(text.splitlines())
        from modules.deliverable_validator import validate_deliverable

        v = validate_deliverable(out_path, task_class="PLAN")
        stub_flags = {"stub": v.stub, "reason": v.reason, "checks": v.checks}

    summary = {
        "success": result.success,
        "tool_steps": result.tool_steps,
        "latency_ms": result.latency_ms,
        "elapsed_s": round(elapsed, 1),
        "deliverable_exists": exists,
        "deliverable_lines": lines,
        "validation": stub_flags,
        "error": result.error,
        "content_preview": (result.content or "")[:400],
    }
    print(json.dumps(summary, indent=2))
    verify_path = os.path.join(LAB, "task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_V3_VERIFY.json")
    with open(verify_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return 0 if result.success and exists and lines >= 80 and not stub_flags.get("stub") else 1


if __name__ == "__main__":
    raise SystemExit(main())

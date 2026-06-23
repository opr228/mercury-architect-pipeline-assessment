"""Core skill loader, trace metrics script, deliverable fabrication verifier."""

import json
import subprocess
import sys
from pathlib import Path

from modules.deliverable_validator import check_fabrication_markers, validate_deliverable
from modules.inception_skill_loader import load_matching_skill, _load_core_skills, _collect_skills


def test_core_skills_always_injected():
    snippet = load_matching_skill("/home/opr", "hello world")
    assert snippet is not None
    assert "dllm-mercury" in snippet.lower()
    assert "oprai-core" in snippet.lower()


def test_roadmap_loads_core_and_domain():
    snippet = load_matching_skill(
        "/home/opr",
        "ROADMAP Mercury2 vs Cursor gap analysis Phase 0 deliverable=results/R.md",
    )
    assert snippet is not None
    assert "dllm-mercury" in snippet.lower()
    assert "mercury-roadmap" in snippet.lower() or "domain skill: mercury-roadmap" in snippet.lower()


def test_load_core_skills_direct():
    skills = _collect_skills("/home/opr")
    core = _load_core_skills(skills)
    assert "lab_target" in core.lower()
    assert "dllm-mercury" in core.lower()


def test_extract_llm_trace_metrics_script():
    out = subprocess.check_output(
        [sys.executable, "/home/opr/scripts/extract_llm_trace_metrics.py", "--last", "3"],
        text=True,
    )
    assert "request_id" in out
    assert "latency_ms" in out
    assert "req_2026" not in out


def test_fabrication_verifier_catches_fake_metrics():
    fake = "# Roadmap\n\nreq_20260623_001 tokens_input=1 tokens_output=2\n" + ("x\n" * 20)
    p = Path("/tmp/test_fabrication_deliverable.md")
    p.write_text(fake)
    try:
        v = validate_deliverable(str(p), task_class="PLAN")
        assert v.stub is True
        assert "fabrication" in v.reason.lower()
        has_fab, reason, labels = check_fabrication_markers(fake)
        assert has_fab is True
        assert labels
    finally:
        p.unlink(missing_ok=True)


def test_fabrication_verifier_allows_real_trace_fields():
    ok_text = (
        "# Audit\n\n| request_id | latency_ms | prompt_tokens |\n"
        "| 066b69f4-6ff5-47e2-8164-e3e75c6b1425 | 9491 | not measured |\n"
        + ("detail line\n" * 20)
    )
    has_fab, _, _ = check_fabrication_markers(ok_text)
    assert has_fab is False

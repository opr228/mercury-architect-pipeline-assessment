"""Skill loader matches audit/consult tasks."""

from modules.inception_skill_loader import load_matching_skill, _score_skill


def test_score_audit_skill():
    score = _score_skill(
        "mercury-ecosystem-audit",
        "Deep OPRAI ecosystem audit",
        "OPRAI ECOSYSTEM AUDIT deliverable=task_history/foo.md",
    )
    assert score >= 3


def test_load_matching_skill_audit():
    snippet = load_matching_skill(
        "/home/opr",
        "OPRAI ECOSYSTEM AUDIT read CODEBASE_MAP deliverable=results/AUDIT.md",
    )
    assert snippet is not None
    assert "dllm-mercury" in snippet.lower()
    assert "ecosystem audit" in snippet.lower() or "mercury-ecosystem-audit" in snippet.lower()


def test_load_matching_skill_consult():
    snippet = load_matching_skill(
        "/home/opr",
        "CONSULT: ARCHITECTURE — how should OPRAI use Mercury?",
    )
    assert snippet is not None
    assert "dllm-mercury" in snippet.lower()
    assert "consult" in snippet.lower()


def test_load_matching_skill_roadmap():
    snippet = load_matching_skill(
        "/home/opr",
        "ROADMAP Mercury2 vs Cursor gap analysis Phase 0 deliverable=results/R.md",
    )
    assert snippet is not None
    assert "dllm-mercury" in snippet.lower()
    assert "roadmap" in snippet.lower()

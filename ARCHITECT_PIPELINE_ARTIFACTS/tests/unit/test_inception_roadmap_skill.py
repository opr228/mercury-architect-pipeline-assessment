"""Roadmap skill and policy gates."""

from modules.inception_agent_policy import (
    _required_roadmap_reads,
    build_evidence_state_block,
    mandatory_evidence_pending,
    task_is_roadmap,
    task_requires_forced_first_tool,
)
from modules.inception_agent_tools import AgentRuntime
from modules.inception_skill_loader import load_matching_skill, _score_skill


def test_task_is_roadmap():
    assert task_is_roadmap("Mercury2 vs Cursor CLI roadmap Phase 0")
    assert not task_is_roadmap("reply with ok")


def test_roadmap_skill_scores_high():
    score = _score_skill(
        "mercury-roadmap",
        "Evidence-based roadmap gap analysis",
        "ROADMAP Mercury2 vs Cursor gap analysis deliverable=foo.md",
    )
    assert score >= 3


def test_load_matching_skill_roadmap():
    snippet = load_matching_skill(
        "/home/opr",
        "ROADMAP Mercury2 vs Cursor CLI gap analysis Phase 0 deliverable=results/R.md",
    )
    assert snippet is not None
    assert "mercury-roadmap" in snippet.lower() or "roadmap" in snippet.lower()
    assert "dllm-mercury" in snippet.lower()


def test_roadmap_mandatory_reads():
    runtime = AgentRuntime(explore_mode=True, allow_writes=True, workspace="/home/opr/oprai_lab")
    msg = "ROADMAP Mercury2 vs Cursor deliverable=task_history/x.md"
    pending = mandatory_evidence_pending(runtime, msg)
    assert pending is not None
    assert "llm_router.py" in pending
    assert "cursor_cli_adapter.py" in pending


def test_roadmap_forced_first_tool_with_deliverable():
    assert task_requires_forced_first_tool(
        "ROADMAP gap analysis deliverable=task_history/x.md"
    )


def test_roadmap_evidence_state_lists_required():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace="/home/opr/oprai_lab",
        read_paths=["/home/opr/oprai_lab/docs/CODEBASE_MAP.md"],
    )
    block = build_evidence_state_block(
        runtime, "ROADMAP Mercury2 vs Cursor deliverable=x.md"
    )
    assert block is not None
    assert "STILL REQUIRED" in block
    assert "inception_adapter" in block
    missing = _required_roadmap_reads(runtime)
    assert "modules/cursor_cli_adapter.py" in missing

"""Dynamic evidence-state block for audit tasks."""

from modules.inception_agent_policy import build_evidence_state_block
from modules.inception_agent_tools import AgentRuntime


def test_evidence_state_lists_still_required_when_trace_unread():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace="/home/opr/oprai_lab",
        read_paths=["/home/opr/oprai_lab/docs/CODEBASE_MAP.md"],
    )
    block = build_evidence_state_block(
        runtime, "OPRAI ECOSYSTEM AUDIT read CODEBASE_MAP llm_trace"
    )
    assert block is not None
    assert "<evidence_state>" in block
    assert "STILL REQUIRED" in block
    assert "llm_trace" in block
    assert "not measured" in block


def test_evidence_state_complete_when_all_read():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace="/home/opr/oprai_lab",
        read_paths=[
            "/home/opr/oprai_lab/docs/CODEBASE_MAP.md",
            "/home/opr/oprai_lab/logs/llm_trace.jsonl",
        ],
    )
    block = build_evidence_state_block(runtime, "OPRAI ECOSYSTEM AUDIT codebase_map")
    assert block is not None
    assert "evidence reads complete" in block.lower() or "evidence complete" in block.lower()


def test_evidence_state_none_for_consult():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=False,
        consult_only=True,
        workspace="/home/opr/oprai_lab",
    )
    assert build_evidence_state_block(runtime, "CONSULT audit ecosystem") is None


def test_evidence_state_none_for_non_audit():
    runtime = AgentRuntime(explore_mode=True, workspace="/home/opr")
    assert build_evidence_state_block(runtime, "where is llm_router defined?") is None

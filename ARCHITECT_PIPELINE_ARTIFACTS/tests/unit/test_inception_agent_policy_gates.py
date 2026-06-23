"""Unit tests for Mercury agent policy gates."""

from modules.inception_agent_policy import (
    deliverable_write_pending,
    mandatory_evidence_pending,
)
from modules.inception_agent_tools import AgentRuntime


def test_mandatory_evidence_pending_audit():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace="/home/opr/oprai_lab",
        read_paths=["/home/opr/oprai_lab/docs/CODEBASE_MAP.md"],
    )
    user = "OPRAI ECOSYSTEM AUDIT v5. read_file CODEBASE_MAP, llm_router, llm_trace"
    msg = mandatory_evidence_pending(runtime, user)
    assert msg is not None
    assert "llm_trace" in msg


def test_mandatory_evidence_pending_inception_policy():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace="/home/opr/oprai_lab",
        read_paths=[
            "/home/opr/oprai_lab/docs/CODEBASE_MAP.md",
            "/home/opr/oprai_lab/logs/llm_trace.jsonl",
        ],
    )
    user = "deep audit inception_agent_policy ecosystem"
    msg = mandatory_evidence_pending(runtime, user)
    assert msg is not None
    assert "inception_agent_policy" in msg


def test_mandatory_evidence_skipped_consult():
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=False,
        consult_only=True,
        workspace="/home/opr/oprai_lab",
    )
    msg = mandatory_evidence_pending(runtime, "ECOSYSTEM AUDIT consult only")
    assert msg is None


def test_deliverable_write_pending_without_write():
    runtime = AgentRuntime(explore_mode=True, allow_writes=True, workspace="/home/opr/oprai_lab")
    msg = deliverable_write_pending(
        "task_history/foo.md",
        runtime,
        "deliverable=task_history/foo.md — write the report",
    )
    assert msg and "write_file" in msg

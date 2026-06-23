"""Forced first-tool routing for audit tasks."""

from modules.inception_agent_policy import (
    task_is_audit,
    task_requires_forced_first_tool,
)


def test_audit_task_detection():
    assert task_is_audit("OPRAI ECOSYSTEM AUDIT v6")
    assert task_is_audit("audit the ecosystem")
    assert not task_is_audit("reply with ok")


def test_forced_first_tool_for_audit():
    assert task_requires_forced_first_tool(
        "OPRAI ECOSYSTEM AUDIT deliverable=task_history/x.md"
    )


def test_forced_first_tool_not_for_consult():
    assert not task_requires_forced_first_tool(
        "CONSULT ARCHITECTURE audit approach", consult_only=True
    )


def test_forced_first_tool_not_for_simple_chat():
    assert not task_requires_forced_first_tool("reply with one word ok")

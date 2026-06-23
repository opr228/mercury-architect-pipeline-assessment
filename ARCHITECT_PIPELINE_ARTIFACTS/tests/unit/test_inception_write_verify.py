"""Post-write validate_deliverable in write_file."""

import json
from unittest.mock import patch

from modules.inception_agent_tools import AgentRuntime, execute_tool


def test_write_file_stub_detection(tmp_path):
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace=str(tmp_path),
    )
    with patch(
        "modules.inception_agent_tools.can_write_path",
        return_value=(True, ""),
    ):
        result = json.loads(
            execute_tool(
                "write_file",
                {"path": "stub.md", "content": "TODO"},
                runtime,
            )
        )
    assert result["ok"] is True
    assert result.get("stub") is True
    assert "stub_reason" in result


def test_write_file_valid_deliverable(tmp_path):
    runtime = AgentRuntime(
        explore_mode=True,
        allow_writes=True,
        workspace=str(tmp_path),
    )
    lines = "\n".join(f"Line {i}: audit evidence paragraph." for i in range(25))
    with patch(
        "modules.inception_agent_tools.can_write_path",
        return_value=(True, ""),
    ):
        result = json.loads(
            execute_tool(
                "write_file",
                {"path": "task_history/report.md", "content": f"# Report\n\n{lines}\n"},
                runtime,
            )
        )
    assert result["ok"] is True
    assert result.get("stub") is False
    assert result["lines"] >= 20

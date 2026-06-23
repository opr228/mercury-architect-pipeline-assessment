"""TaskRunner unit tests with mocked call_agent."""

from unittest.mock import patch

from modules.inception_adapter import InceptionResult
from modules.task_runner import PhaseResult, TaskRunner, TaskSpec


def test_run_phase_success_without_deliverable(tmp_path):
    runner = TaskRunner(str(tmp_path))
    spec = TaskSpec(
        task_id="t1",
        phase="DESIGN",
        workspace=str(tmp_path),
        message="Phase=DESIGN ARCHITECTURE test",
        explore_mode=True,
        allow_writes=False,
    )
    with patch("modules.task_runner.call_agent") as mock_agent:
        mock_agent.return_value = InceptionResult(
            success=True,
            content="ADR in chat",
            model="mercury-2",
            tool_steps=3,
        )
        result = runner.run_phase(spec)
    assert result.success is True
    assert result.phase == "DESIGN"
    assert result.tool_steps == 3


def test_run_pipeline_stops_on_failure(tmp_path):
    runner = TaskRunner(str(tmp_path))
    phases = [
        TaskSpec(task_id="t1", phase="DESIGN", workspace=str(tmp_path), message="Phase=DESIGN"),
        TaskSpec(task_id="t1", phase="PLAN", workspace=str(tmp_path), message="Phase=PLAN"),
    ]
    with patch("modules.task_runner.call_agent") as mock_agent:
        mock_agent.side_effect = [
            InceptionResult(success=False, content="", model="m", error="fail", tool_steps=1),
            InceptionResult(success=True, content="ok", model="m", tool_steps=1),
        ]
        results = runner.run_pipeline(phases)
    assert len(results) == 1
    assert results[0].success is False

"""TaskRunner — phase state machine over Inception Mercury call_agent."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from modules.deliverable_validator import validate_deliverable
from modules.inception_adapter import call_agent, InceptionResult
from modules.inception_agent_policy import phase_agent_budgets
from modules.plan_validator import validate_plan


@dataclass
class TaskSpec:
    task_id: str
    phase: str
    workspace: str
    message: str
    deliverable: str | None = None
    plan_path: str | None = None
    explore_mode: bool = True
    allow_writes: bool = False
    max_tokens: int = 8192


@dataclass
class PhaseResult:
    success: bool
    phase: str
    deliverable_path: str | None
    validation: dict[str, Any] = field(default_factory=dict)
    tool_steps: int = 0
    error: str | None = None
    content_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskRunner:
    """Run architect pipeline phases sequentially with checkpoints."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def _checkpoint_path(self, task_id: str) -> Path:
        return Path(self.workspace) / "task_history/oprai_improve_lab/results" / f"{task_id}_checkpoint.json"

    def _save_checkpoint(self, task_id: str, results: List[PhaseResult]) -> None:
        path = self._checkpoint_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "phases": [r.to_dict() for r in results],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def run_phase(self, spec: TaskSpec) -> PhaseResult:
        steps, gates = phase_agent_budgets(spec.phase)
        prev_env: dict[str, str | None] = {
            "INCEPTION_AGENT_MAX_STEPS": os.environ.get("INCEPTION_AGENT_MAX_STEPS"),
            "INCEPTION_MAX_GATE_TURNS": os.environ.get("INCEPTION_MAX_GATE_TURNS"),
            "CURSOR_AGENT_WORKSPACE": os.environ.get("CURSOR_AGENT_WORKSPACE"),
            "OPRAI_INSTANCE_ROOT": os.environ.get("OPRAI_INSTANCE_ROOT"),
        }
        os.environ["INCEPTION_AGENT_MAX_STEPS"] = str(steps)
        os.environ["INCEPTION_MAX_GATE_TURNS"] = str(gates)
        os.environ["CURSOR_AGENT_WORKSPACE"] = spec.workspace
        os.environ["OPRAI_INSTANCE_ROOT"] = spec.workspace
        if spec.plan_path:
            os.environ["OPRAI_PLAN_PATH"] = spec.plan_path

        try:
            result: InceptionResult = call_agent(
                messages=[{"role": "user", "content": spec.message}],
                max_tokens=spec.max_tokens,
                explore_mode=spec.explore_mode,
                allow_writes=spec.allow_writes,
                timeout_seconds=int(os.getenv("INCEPTION_AGENT_TIMEOUT_SECONDS", "600")),
            )
        finally:
            for key, val in prev_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

        deliverable_path = spec.deliverable
        validation: dict[str, Any] = {}
        if deliverable_path:
            full = str(Path(spec.workspace) / deliverable_path)
            is_roadmap = (
                "roadmap" in spec.message.lower()
                or "ROADMAP" in Path(deliverable_path).name.upper()
            )
            if spec.phase.upper() == "PLAN" and not is_roadmap:
                pr = validate_plan(full)
                validation = pr.to_dict()
                success = result.success and pr.valid and not pr.stub
            else:
                task_class = "VERIFY" if deliverable_path.lower().endswith(".json") else "IMPLEMENT"
                vr = validate_deliverable(full, task_class=task_class)
                validation = vr.to_dict()
                success = result.success and vr.pass_gate
        else:
            success = result.success

        return PhaseResult(
            success=success,
            phase=spec.phase,
            deliverable_path=deliverable_path,
            validation=validation,
            tool_steps=result.tool_steps,
            error=result.error,
            content_preview=(result.content or "")[:400],
        )

    def run_pipeline(
        self,
        phases: List[TaskSpec],
        *,
        checkpoint_after_each: bool = True,
        stop_on_failure: bool = True,
    ) -> List[PhaseResult]:
        results: List[PhaseResult] = []
        task_id = phases[0].task_id if phases else "unknown"
        for spec in phases:
            pr = self.run_phase(spec)
            results.append(pr)
            if checkpoint_after_each:
                self._save_checkpoint(task_id, results)
            if stop_on_failure and not pr.success:
                break
        return results

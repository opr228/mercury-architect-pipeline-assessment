"""Central path resolution for OPRAI prod vs lab vs project instances."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, Tuple

_DEFAULT_ROOT = "/home/opr"
TargetKind = Literal["prod", "lab", "project", "remote"]

INSTANCE_ROOT = Path(os.getenv("OPRAI_INSTANCE_ROOT", _DEFAULT_ROOT).rstrip("/"))
LAB_ROOT = Path(os.getenv("OPRAI_LAB_ROOT", str(INSTANCE_ROOT / "oprai_lab")).rstrip("/"))
PROJECTS_ROOT = Path(os.getenv("OPRAI_PROJECTS_ROOT", str(INSTANCE_ROOT / "projects")).rstrip("/"))


class TargetConflictError(ValueError):
    """Mutually exclusive chat targets (lab / local project / remote)."""


def instance_root_str() -> str:
    return str(INSTANCE_ROOT)


def lab_or_prod(lab_target: bool = False) -> Path:
    """Return lab root when targeting lab writes; otherwise prod instance root."""
    return LAB_ROOT if lab_target else INSTANCE_ROOT


def _is_lab_workspace(workspace: str) -> bool:
    ws = Path(workspace).resolve()
    if ws.name == "oprai_lab":
        return True
    try:
        prod_lab = Path(_DEFAULT_ROOT).resolve() / "oprai_lab"
        if ws == prod_lab.resolve():
            return True
    except OSError:
        pass
    try:
        if ws == LAB_ROOT.resolve():
            return True
    except OSError:
        pass
    return False


def normalize_workspace_relative_path(raw: str, workspace: str) -> str:
    """Strip redundant oprai_lab/ prefix when workspace is already the lab root."""
    text = (raw or "").strip().strip("`\"'")
    if not text:
        return text
    ws = Path(workspace).resolve()
    path = Path(text).expanduser()
    if path.is_absolute():
        try:
            resolved = path.resolve()
            nested = (ws / "oprai_lab").resolve()
            if str(resolved).startswith(str(nested) + os.sep):
                return str(ws / resolved.relative_to(nested))
        except (OSError, ValueError):
            pass
        return text
    if not _is_lab_workspace(workspace):
        return text
    normalized = text.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while normalized.startswith("oprai_lab/"):
        normalized = normalized[len("oprai_lab/") :]
    return normalized


def resolve_deliverable_path(raw: str, workspace: str) -> Path:
    """Normalize and resolve a deliverable path against the agent workspace."""
    normalized = normalize_workspace_relative_path(raw, workspace)
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = Path(workspace).resolve() / path
    return path.resolve()


def project_root(project_id: str) -> Path:
    """Canonical project root from registry."""
    from modules.project_registry import resolve_project

    return resolve_project(project_id).root


def resolve_write_target(
    *,
    lab_target: bool = False,
    remote_target: bool = False,
    project_id: Optional[str] = None,
    enable_lab_target: bool = True,
    instance_root: Optional[Path] = None,
) -> Tuple[Path, TargetKind]:
    """
    Resolve workspace for delegate/chat runtime.
    Mutual exclusion: exactly one of lab_target, local project_id, remote_target+project_id.
    """
    pid = (project_id or "").strip()
    if lab_target and remote_target:
        raise TargetConflictError("lab_target and remote_target are mutually exclusive")
    if lab_target and pid:
        raise TargetConflictError("lab_target and project_id are mutually exclusive")
    if remote_target and not pid:
        raise TargetConflictError("remote_target requires project_id")
    if remote_target:
        from modules.project_registry import resolve_remote_project

        resolve_remote_project(pid)
        return LAB_ROOT, "remote"
    if pid:
        from modules.project_registry import resolve_project

        entry = resolve_project(pid)
        if entry.is_remote:
            raise TargetConflictError(
                f"project {pid} is remote; set remote_target: true"
            )
        root = entry.root
        if root is None:
            raise TargetConflictError(f"project {pid} has no local root")
        return root, "project"
    if enable_lab_target and lab_target:
        return LAB_ROOT, "lab"
    return (instance_root or INSTANCE_ROOT), "prod"

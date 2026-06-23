"""OPRAI tool definitions and executor for Inception Mercury agent loop."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.codebase_context import is_path_allowed, resolve_cli_profile
from modules.instance_paths import instance_root_str, normalize_workspace_relative_path
from modules.inception_tool_helpers import normalize_tool_args


@dataclass
class AgentRuntime:
    explore_mode: bool = False
    allow_writes: bool = False
    consult_only: bool = False
    lab_target: bool = False
    workspace: str = field(default_factory=instance_root_str)
    task_phase: Optional[str] = None
    plan_path: Optional[str] = None
    read_count: int = 0
    max_reads_lean: int = 3
    tools_called: List[str] = field(default_factory=list)
    write_paths: List[str] = field(default_factory=list)
    read_paths: List[str] = field(default_factory=list)
    shell_commands: List[str] = field(default_factory=list)
    diff_lines: int = 0
    tdd_red_seen: bool = False
    tdd_green_seen: bool = False


def _log_tool_change(path: str, *, action: str) -> None:
    try:
        from modules.agent_activity import _log

        request_id = os.getenv("OPRAI_REQUEST_ID", "").strip()
        if request_id:
            _log(request_id, "change", f"{action} {path}")
    except Exception:
        pass


def _workspace_path(workspace: str) -> Path:
    return Path(workspace).resolve()


def _resolve_path(raw: str, workspace: str) -> Path:
    normalized = normalize_workspace_relative_path(raw, workspace)
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = _workspace_path(workspace) / path
    return path.resolve()


def _load_permission_patterns(workspace: str, profile: str) -> Tuple[List[str], List[str]]:
    config_path = _workspace_path(workspace) / ".cursor" / f"cli.{profile}.json"
    if not config_path.is_file():
        return [], []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    perms = data.get("permissions") or {}
    allow = [str(x) for x in perms.get("allow", []) if x]
    deny = [str(x) for x in perms.get("deny", []) if x]
    return allow, deny


def _match_permission(path: str, entry: str) -> bool:
    """Match Cursor-style permission entry like Read(/home/opr/docs/**)."""
    m = re.match(r"^(Read|Write|Shell)\((.+)\)$", entry.strip())
    if not m:
        return False
    pattern = m.group(2).strip()
    normalized = path.replace("\\", "/")
    if pattern.endswith("(/**)") or "(**)" in pattern:
        prefix = pattern.split("(")[0].rstrip("/")
        return normalized.startswith(prefix) or fnmatch.fnmatch(normalized, pattern.replace("(**)", "*"))
    return fnmatch.fnmatch(normalized, pattern) or normalized.startswith(pattern.rstrip("*").rstrip("/"))


def _permission_allows(path: str, kind: str, workspace: str, profile: str) -> bool:
    allow, deny = _load_permission_patterns(workspace, profile)
    for entry in deny:
        if entry.startswith(f"{kind}(") and _match_permission(path, entry):
            return False
    if not allow:
        return kind != "Write"
    for entry in allow:
        if entry.startswith(f"{kind}(") and _match_permission(path, entry):
            return True
    return False


def can_read_path(path: str, runtime: AgentRuntime) -> Tuple[bool, str]:
    mode = "explore" if runtime.explore_mode else "lean"
    if mode == "explore":
        if not is_path_allowed(path, mode=mode):
            return False, f"path not allowed in explore mode: {path}"
        return True, ""
    if is_path_allowed(path, mode="lean"):
        return True, ""
    profile = resolve_cli_profile(explore_mode=False)
    if _permission_allows(path, "Read", runtime.workspace, profile):
        return True, ""
    return False, f"path not allowed in lean mode: {path}"


def can_write_path(path: str, runtime: AgentRuntime) -> Tuple[bool, str]:
    if runtime.consult_only:
        return False, "write_file blocked in CONSULT-only mode"
    phase = (runtime.task_phase or "").upper()
    if phase == "IMPLEMENT" and not runtime.allow_writes:
        return False, "Phase=IMPLEMENT requires /api/autonomy/arm first"
    if not runtime.allow_writes:
        return False, "writes not armed — call /api/autonomy/arm first"
    if not runtime.explore_mode:
        return False, "write_file requires explore_mode"
    profile = resolve_cli_profile(explore_mode=True)
    if not _permission_allows(path, "Write", runtime.workspace, profile):
        return False, f"path not allowed for Write in cli.{profile}: {path}"
    return True, ""


def can_run_shell(runtime: AgentRuntime) -> Tuple[bool, str]:
    if not runtime.explore_mode:
        return False, "run_command requires explore_mode"
    profile = resolve_cli_profile(explore_mode=True)
    allow, deny = _load_permission_patterns(runtime.workspace, profile)
    for entry in deny:
        if entry.startswith("Shell("):
            return False, "shell denied by cli profile"
    if any(entry.startswith("Shell(") for entry in allow):
        return True, ""
    return False, "shell not allowed in current cli profile"


def tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a UTF-8 text file. Lean mode: only read_allowlist paths, max 3 reads. "
                    "Explore mode: broader reads; never read env.secrets, env.local, .env."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or workspace-relative file path"},
                        "offset": {"type": "integer", "description": "1-based start line (optional)"},
                        "limit": {"type": "integer", "description": "Max lines to return (optional)"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files and directories at a path (non-recursive).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep_search",
                "description": "Search file contents with ripgrep (rg). Lean: allowlist paths only.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regex/search pattern (alias: query)",
                        },
                        "path": {"type": "string", "description": "File or directory to search"},
                        "head_limit": {"type": "integer", "description": "Max matching lines", "default": 40},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": (
                    "Run a shell command in the agent workspace. Explore mode only. "
                    "No secrets, no destructive prod commands."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "default": 60},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob_search",
                "description": "Find files by glob pattern under a directory (e.g. **/*.py). Max 80 paths.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern such as **/*.py"},
                        "path": {"type": "string", "description": "Root directory", "default": "."},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": (
                    "Replace exact old_string with new_string in a file (single occurrence). "
                    "Requires explore_mode + autonomy armed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "Write UTF-8 text to a file. Requires explore_mode + autonomy armed. "
                    "Typically allowed: oprai_lab/** only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]


def build_capabilities_system_message(runtime: AgentRuntime) -> str:
    """Legacy single-block message; prefer build_agent_system_messages."""
    from modules.inception_agent_policy import build_agent_system_messages

    return "\n\n".join(build_agent_system_messages(runtime))


def execute_tool(name: str, arguments: Dict[str, Any], runtime: AgentRuntime) -> str:
    try:
        runtime.tools_called.append(name)
        args = normalize_tool_args(name, arguments if isinstance(arguments, dict) else {})
        if name == "read_file":
            return _tool_read_file(args, runtime)
        if name == "list_directory":
            return _tool_list_directory(args, runtime)
        if name == "glob_search":
            return _tool_glob_search(args, runtime)
        if name == "grep_search":
            return _tool_grep(args, runtime)
        if name == "run_command":
            return _tool_run_command(args, runtime)
        if name == "edit_file":
            return _tool_edit_file(args, runtime)
        if name == "write_file":
            return _tool_write_file(args, runtime)
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as exc:
        return json.dumps({"error": str(exc)[:500]})


def _tool_read_file(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    path = _resolve_path(str(args.get("path", "")), runtime.workspace)
    ok, reason = can_read_path(str(path), runtime)
    if not ok:
        return json.dumps({"error": reason})
    if not runtime.explore_mode:
        runtime.read_count += 1
        if runtime.read_count > runtime.max_reads_lean:
            return json.dumps({"error": f"lean mode read limit ({runtime.max_reads_lean}) exceeded"})
    if not path.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    offset = int(args.get("offset") or 1)
    limit = args.get("limit")
    if offset > 1 or limit:
        start = max(0, offset - 1)
        end = start + int(limit) if limit else None
        lines = lines[start:end]
        text = "\n".join(lines)
    if len(text) > 12000:
        text = text[:12000] + "\n[...truncated]"
    runtime.read_paths.append(str(path))
    lowered = str(path).lower()
    if "llm_trace" in lowered or "trace_snippet" in lowered or "_roadmap_v3_trace" in lowered:
        if not any("llm_trace" in p.lower() for p in runtime.read_paths[:-1]):
            runtime.read_paths.append("logs/llm_trace.jsonl")
    return json.dumps({"path": str(path), "content": text, "lines": len(lines)})


def _tool_list_directory(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    path = _resolve_path(str(args.get("path", ".")), runtime.workspace)
    ok, reason = can_read_path(str(path), runtime)
    if not ok:
        return json.dumps({"error": reason})
    if not path.is_dir():
        return json.dumps({"error": f"not a directory: {path}"})
    entries = sorted(path.iterdir(), key=lambda p: p.name)[:200]
    return json.dumps({"path": str(path), "entries": [p.name + ("/" if p.is_dir() else "") for p in entries]})


def _tool_grep(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return json.dumps({"error": "pattern required"})
    raw_path = str(args.get("path") or runtime.workspace)
    path = _resolve_path(raw_path, runtime.workspace)
    ok, reason = can_read_path(str(path), runtime)
    if not ok:
        return json.dumps({"error": reason})
    head_limit = int(args.get("head_limit") or 40)
    cmd = ["rg", "-n", "--no-heading", "-m", str(head_limit), pattern, str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=runtime.workspace)
    except FileNotFoundError:
        return json.dumps({"error": "rg not installed"})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "grep timeout"})
    output = (proc.stdout or proc.stderr or "").strip()
    if len(output) > 8000:
        output = output[:8000] + "\n[...truncated]"
    return json.dumps({"matches": output, "exit_code": proc.returncode})


def _tool_run_command(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    ok, reason = can_run_shell(runtime)
    if not ok:
        return json.dumps({"error": reason})
    command = str(args.get("command", "")).strip()
    if not command:
        return json.dumps({"error": "command required"})
    lowered = command.lower()
    for blocked in ("env.secrets", "env.local", "rm -rf /", "mkfs", "shutdown", "reboot"):
        if blocked in lowered:
            return json.dumps({"error": f"command blocked: contains {blocked}"})
    timeout = int(args.get("timeout_seconds") or 60)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=runtime.workspace,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"timeout after {timeout}s"})
    runtime.shell_commands.append(command)
    if "extract_llm_trace_metrics" in lowered:
        runtime.read_paths.append("logs/llm_trace.jsonl")
    if "trace_snippet" in lowered or "_roadmap_v3_trace" in lowered:
        runtime.read_paths.append("logs/llm_trace.jsonl")
    if "pytest" in lowered and (runtime.task_phase or "").upper() == "IMPLEMENT":
        if proc.returncode != 0:
            runtime.tdd_red_seen = True
        else:
            runtime.tdd_green_seen = True
    stdout = (proc.stdout or "")[:6000]
    stderr = (proc.stderr or "")[:2000]
    return json.dumps({"exit_code": proc.returncode, "stdout": stdout, "stderr": stderr})


def _tool_glob_search(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return json.dumps({"error": "pattern required"})
    root = _resolve_path(str(args.get("path") or "."), runtime.workspace)
    ok, reason = can_read_path(str(root), runtime)
    if not ok:
        return json.dumps({"error": reason})
    if not root.is_dir():
        return json.dumps({"error": f"not a directory: {root}"})
    matches: List[str] = []
    for hit in sorted(root.glob(pattern))[:80]:
        rel = str(hit)
        ok_hit, _ = can_read_path(rel, runtime)
        if ok_hit:
            matches.append(rel)
    return json.dumps({"pattern": pattern, "root": str(root), "matches": matches, "count": len(matches)})


def _resolve_max_diff_lines(runtime: AgentRuntime) -> int | None:
    if not runtime.plan_path:
        return None
    try:
        from modules.instance_paths import resolve_deliverable_path
        from modules.plan_validator import max_diff_lines_from_plan

        resolved = resolve_deliverable_path(runtime.plan_path, runtime.workspace)
        return max_diff_lines_from_plan(str(resolved))
    except Exception:
        return None


def _tool_edit_file(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    path = _resolve_path(str(args.get("path", "")), runtime.workspace)
    ok, reason = can_write_path(str(path), runtime)
    if not ok:
        return json.dumps({"error": reason})
    if not path.is_file():
        return json.dumps({"error": f"file not found: {path}"})
    old_string = str(args.get("old_string", ""))
    new_string = str(args.get("new_string", ""))
    if not old_string:
        return json.dumps({"error": "old_string required"})
    content = path.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_string)
    if count == 0:
        return json.dumps({"error": "old_string not found (match whitespace exactly)"})
    if count > 1:
        return json.dumps({"error": f"old_string found {count} times; add more context"})
    delta = max(len(old_string.splitlines()), len(new_string.splitlines()), 1)
    max_diff = _resolve_max_diff_lines(runtime)
    if max_diff is not None and runtime.diff_lines + delta > max_diff:
        return json.dumps({
            "error": "diff_budget_exceeded",
            "max_diff_lines": max_diff,
            "current_diff_lines": runtime.diff_lines,
            "attempted_delta": delta,
        })
    path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
    runtime.diff_lines += delta
    runtime.write_paths.append(str(path))
    _log_tool_change(str(path), action="edit_file")
    return json.dumps({"ok": True, "path": str(path), "replacements": 1})


def _tool_write_file(args: Dict[str, Any], runtime: AgentRuntime) -> str:
    path = _resolve_path(str(args.get("path", "")), runtime.workspace)
    ok, reason = can_write_path(str(path), runtime)
    if not ok:
        return json.dumps({"error": reason})
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    runtime.write_paths.append(str(path))
    _log_tool_change(str(path), action="write_file")
    from modules.deliverable_validator import validate_deliverable

    name = path.name.upper()
    if name.startswith("PLAN"):
        task_class = "PLAN"
    elif name.startswith("VERIFY") or path.suffix.lower() == ".json":
        task_class = "VERIFY"
    else:
        task_class = "IMPLEMENT"
    validation = validate_deliverable(path, task_class=task_class)
    line_count = len(content.splitlines())
    payload: Dict[str, Any] = {
        "ok": True,
        "path": str(path),
        "bytes": len(content.encode("utf-8")),
        "lines": line_count,
        "stub": validation.stub,
    }
    if validation.stub:
        payload["stub_reason"] = validation.reason
    if validation.checks.get("fabrication"):
        payload["fabrication"] = validation.checks["fabrication"]
    return json.dumps(payload)

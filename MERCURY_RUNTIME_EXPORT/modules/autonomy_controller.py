#!/usr/bin/env python3
"""Shared commanded-autonomy controls for root+ORK runtimes."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


from modules.instance_paths import instance_root_str

STATE_FILE = os.getenv("AUTONOMY_STATE_FILE", f"{instance_root_str()}/autonomy_state.json")
REPORT_FILE = os.getenv("AUTONOMY_REPORT_FILE", f"{instance_root_str()}/autonomy_report.jsonl")
INSTANCE_ROOT = instance_root_str()
DEFAULT_ALLOWLIST = (
    f"{INSTANCE_ROOT}/ORKESTRATOROPRAI100/",
    f"{INSTANCE_ROOT}/OPRAI14/",
    f"{INSTANCE_ROOT}/modules/",
    f"{INSTANCE_ROOT}/orchestrator_api.py",
    f"{INSTANCE_ROOT}/ORKESTRATOROPRAI100/orchestrator_api.py",
    f"{INSTANCE_ROOT}/task_history/",
    f"{INSTANCE_ROOT}/scripts/",
    f"{INSTANCE_ROOT}/.cursor/skills/",
)
DEFAULT_DENY_SUBSTRINGS = (
    "/etc/systemd/",
    ".service",
    ".env",
    "env.local",
    ".openrouter_key",
    ".ssh/",
    ".gnupg/",
    "id_rsa",
    "id_ed25519",
)


class AutonomyController:
    def __init__(self, state_file: str = STATE_FILE, report_file: str = REPORT_FILE):
        self.state_file = Path(state_file)
        self.report_file = Path(report_file)
        self._lock = threading.RLock()
        self.state = self._load_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "mode": "propose",
            "armed_until": 0.0,
            "approval_token_hash": "",
            "max_steps": 0,
            "max_files": 0,
            "max_llm_calls": 0,
            "remaining_steps": 0,
            "remaining_files": 0,
            "remaining_llm_calls": 0,
            "reason": "",
            "last_operator": "unknown",
            "updated_at": datetime.utcnow().isoformat(),
            "allowlist": list(DEFAULT_ALLOWLIST),
            "deny_substrings": list(DEFAULT_DENY_SUBSTRINGS),
            "last_run_id": "",
        }

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged = self._default_state()
                    merged.update(data)
                    return merged
            except Exception:
                pass
        state = self._default_state()
        self._save_state(state)
        return state

    def _save_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        payload = state if state is not None else self.state
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""

    def _is_autonomy_enabled(self) -> bool:
        return os.getenv("AUTONOMY_ENABLED", "1") == "1"

    def _is_self_modify_enabled(self) -> bool:
        return os.getenv("SELF_MODIFY_ENABLED", "0") == "1"

    def _ttl_valid(self) -> bool:
        return float(self.state.get("armed_until") or 0.0) > time.time()

    def _consume_mode_end(self) -> None:
        if self.state.get("mode") == "apply_once" and int(self.state.get("remaining_steps") or 0) <= 0:
            self.disarm("apply_once_consumed")

    def _report(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        rec = {
            "ts": datetime.utcnow().isoformat(),
            "event": event,
            "mode": self.state.get("mode"),
            "armed_until": self.state.get("armed_until"),
            "payload": payload or {},
        }
        with self.report_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def arm(
        self,
        *,
        mode: str,
        ttl_seconds: int,
        approval_token: str,
        max_steps: int,
        max_files: int,
        max_llm_calls: int,
        reason: str,
        operator: str,
    ) -> Dict[str, Any]:
        if mode not in ("apply_once", "apply_window"):
            raise ValueError("mode must be apply_once or apply_window")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        with self._lock:
            self.state["mode"] = mode
            self.state["armed_until"] = time.time() + int(ttl_seconds)
            self.state["approval_token_hash"] = self._hash_token(approval_token)
            self.state["max_steps"] = int(max_steps)
            self.state["max_files"] = int(max_files)
            self.state["max_llm_calls"] = int(max_llm_calls)
            self.state["remaining_steps"] = int(max_steps)
            self.state["remaining_files"] = int(max_files)
            self.state["remaining_llm_calls"] = int(max_llm_calls)
            self.state["reason"] = reason
            self.state["last_operator"] = operator or "unknown"
            self.state["updated_at"] = datetime.utcnow().isoformat()
            self._save_state()
            self._report("autonomy_arm", self.snapshot())
            return self.snapshot()

    def disarm(self, reason: str = "manual") -> Dict[str, Any]:
        with self._lock:
            self.state["mode"] = "propose"
            self.state["armed_until"] = 0.0
            self.state["approval_token_hash"] = ""
            self.state["remaining_steps"] = 0
            self.state["remaining_files"] = 0
            self.state["remaining_llm_calls"] = 0
            self.state["reason"] = reason
            self.state["updated_at"] = datetime.utcnow().isoformat()
            self._save_state()
            self._report("autonomy_disarm", {"reason": reason})
            return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        snap = copy.deepcopy(self.state)
        snap["ttl_seconds_left"] = max(0, int(float(self.state.get("armed_until") or 0.0) - time.time()))
        snap["autonomy_enabled"] = self._is_autonomy_enabled()
        snap["self_modify_enabled"] = self._is_self_modify_enabled()
        return snap

    def is_apply_allowed(self, approval_token: str = "") -> (bool, str):
        if not self._is_autonomy_enabled():
            return False, "AUTONOMY_ENABLED=0"
        if not self._is_self_modify_enabled():
            return False, "SELF_MODIFY_ENABLED=0"
        if self.state.get("mode") not in ("apply_once", "apply_window"):
            return False, "mode_is_propose"
        if not self._ttl_valid():
            return False, "ttl_expired"
        expected = self.state.get("approval_token_hash") or ""
        if expected and self._hash_token(approval_token) != expected:
            return False, "invalid_approval_token"
        return True, "ok"

    def consume_step_budget(self, count: int = 1) -> bool:
        with self._lock:
            rem = int(self.state.get("remaining_steps") or 0)
            if rem < count:
                return False
            self.state["remaining_steps"] = rem - count
            self.state["updated_at"] = datetime.utcnow().isoformat()
            self._consume_mode_end()
            self._save_state()
            return True

    def consume_file_budget(self, count: int = 1) -> bool:
        with self._lock:
            rem = int(self.state.get("remaining_files") or 0)
            if rem < count:
                return False
            self.state["remaining_files"] = rem - count
            self.state["updated_at"] = datetime.utcnow().isoformat()
            self._save_state()
            return True

    def is_path_allowed(self, target_path: str) -> (bool, str):
        normalized = str(Path(target_path).resolve())
        for deny in self.state.get("deny_substrings", []):
            if deny and deny in normalized:
                return False, f"denylist:{deny}"
        allowlist = self.state.get("allowlist") or []
        for allow in allowlist:
            allow_abs = str(Path(allow).resolve()) if allow.startswith("/") else allow
            if normalized.startswith(allow_abs):
                return True, "ok"
        return False, "path_not_in_allowlist"

    def recent_reports(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.report_file.exists():
            return []
        lines = self.report_file.read_text(encoding="utf-8").splitlines()
        items: List[Dict[str, Any]] = []
        for line in lines[-max(1, limit):]:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
        return items

    def run_transactional_update(
        self,
        *,
        target_path: str,
        old_text: str,
        new_text: str,
        run_compile_check: bool = True,
        autonomy_run_id: str = "",
        kpi_delta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        allowed, reason = self.is_path_allowed(target_path)
        if not allowed:
            self._report("autonomy_reject", {"reason": reason, "target_path": target_path})
            return {"success": False, "error": reason}
        if not self.consume_file_budget(1):
            self._report("autonomy_reject", {"reason": "file_budget_exhausted"})
            return {"success": False, "error": "file_budget_exhausted"}
        if not self.consume_step_budget(1):
            self._report("autonomy_reject", {"reason": "step_budget_exhausted"})
            return {"success": False, "error": "step_budget_exhausted"}
        path = Path(target_path)
        if not path.exists():
            return {"success": False, "error": f"target_not_found:{target_path}"}
        original = path.read_text(encoding="utf-8")
        if old_text and old_text not in original:
            return {"success": False, "error": "old_text_not_found"}
        updated = original.replace(old_text, new_text, 1) if old_text else new_text
        backup_file = Path(tempfile.mkstemp(prefix=f"autonomy_backup_{path.name}_", suffix=".bak")[1])
        backup_file.write_text(original, encoding="utf-8")
        try:
            path.write_text(updated, encoding="utf-8")
            if run_compile_check and target_path.endswith(".py"):
                result = subprocess.run(
                    ["python3", "-m", "py_compile", target_path],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"compile_failed:{result.stderr.strip()[:300]}")
            self._report(
                "autonomy_run_result",
                {
                    "success": True,
                    "target_path": target_path,
                    "backup_file": str(backup_file),
                    "run_id": autonomy_run_id,
                    "validation_passed": True,
                    "kpi_delta": kpi_delta or {},
                },
            )
            return {"success": True, "backup_file": str(backup_file), "target_path": target_path}
        except Exception as exc:
            path.write_text(original, encoding="utf-8")
            self._report(
                "autonomy_run_result",
                {
                    "success": False,
                    "target_path": target_path,
                    "error": str(exc),
                    "run_id": autonomy_run_id,
                    "validation_passed": False,
                    "kpi_delta": kpi_delta or {},
                },
            )
            return {"success": False, "error": str(exc), "rolled_back": True}


_controller_singleton: Optional[AutonomyController] = None


def get_autonomy_controller() -> AutonomyController:
    global _controller_singleton
    if _controller_singleton is None:
        _controller_singleton = AutonomyController()
    return _controller_singleton

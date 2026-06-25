"""OPRAI Context Layer — curated codebase navigation without LLM."""

from __future__ import annotations

import fnmatch
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.instance_paths import INSTANCE_ROOT

WORKSPACE = Path(os.getenv("CURSOR_AGENT_WORKSPACE", str(INSTANCE_ROOT)))
INDEX_PATH = WORKSPACE / "agent_registry" / "codebase_index.json"
MAP_PATH = WORKSPACE / "docs" / "CODEBASE_MAP.md"

_DEFAULT_DENY_SUBSTRINGS = (
    "/etc/systemd/",
    ".service",
    ".env",
    "env.local",
    "env.secrets",
    "env.cursor.secret",
    ".openrouter_key",
    ".ssh/",
    ".gnupg/",
    "id_rsa",
    "id_ed25519",
    ".cursor-server/",
    ".cache/",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


    except ValueError:
        return default


_index_cache: tuple[float, float, Dict[str, Any]] | None = None  # (monotonic_ts, file_mtime, data)


def clear_index_cache() -> None:
    """Clear in-memory index cache (for tests)."""
    global _index_cache
    _index_cache = None


def _load_index_uncached() -> Dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {
            "version": 0,
            "generated_at": None,
            "entrypoints": [],
            "modules": [],
            "deny_globs": [],
            "read_allowlist": [],
            "flows": [],
        }
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def load_index() -> Dict[str, Any]:
    global _index_cache
    ttl = _env_int("OPRAI_CONTEXT_CACHE_TTL_SECONDS", 60)
    if ttl <= 0:
        return _load_index_uncached()
    now = time.monotonic()
    file_mtime = INDEX_PATH.stat().st_mtime if INDEX_PATH.is_file() else 0.0
    if _index_cache is not None:
        cached_ts, cached_mtime, cached_data = _index_cache
        if (now - cached_ts) < ttl and cached_mtime == file_mtime:
            return cached_data
    data = _load_index_uncached()
    _index_cache = (now, file_mtime, data)
    return data


def load_map_excerpt(max_chars: int) -> str:
    if not MAP_PATH.is_file():
        return ""
    text = MAP_PATH.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n[...truncated]"


def load_research_excerpt(max_chars: Optional[int] = None) -> str:
    """Load synthesis-first research excerpt for prompt injection."""
    artifact = os.getenv("OPRAI_RESEARCH_ARTIFACT", "").strip()
    if not artifact:
        return ""
    path = Path(artifact)
    if not path.is_file():
        return ""
    limit = max_chars if max_chars is not None else _env_int("OPRAI_RESEARCH_MAX_CHARS", 4000)
    sidecar_data: Optional[Dict[str, Any]] = None
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        try:
            sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            sidecar_data = None
    try:
        from modules.research_synthesis import extract_injection_excerpt

        text = extract_injection_excerpt(path.read_text(encoding="utf-8", errors="replace"), sidecar_data, limit)
    except Exception:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > limit:
            text = text[: limit - 20].rstrip() + "\n[...truncated]"
    return text.strip()


def load_research_metrics() -> Dict[str, Any]:
    """Sidecar metrics for panel / context status."""
    artifact = os.getenv("OPRAI_RESEARCH_ARTIFACT", "").strip()
    if not artifact:
        return {"status": "off"}
    sidecar = Path(artifact).with_suffix(".json")
    if not sidecar.is_file():
        return {"status": "unknown", "artifact": artifact}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "error", "artifact": artifact}
    degraded = bool(data.get("degraded"))
    relevant = int(data.get("relevant_fetches", 0) or 0)
    if relevant > 0 and not degraded:
        chip = "ok"
    elif data.get("providers_skipped"):
        chip = "blocked"
    elif degraded:
        chip = "degraded"
    else:
        chip = "ok"
    return {
        "status": chip,
        "artifact": artifact,
        "artifact_json": str(sidecar),
        "degraded": degraded,
        "searches_ok": data.get("searches_ok"),
        "searches_total": data.get("searches_total"),
        "relevant_fetches": relevant,
        "providers_skipped": data.get("providers_skipped") or [],
        "adopted_count": data.get("adopted_count", 0),
        "rejected_count": data.get("rejected_count", 0),
        "defer_count": data.get("defer_count", 0),
        "blocklist_path": data.get("blocklist_path"),
    }


def _compact_index_summary(index: Dict[str, Any]) -> str:
    lines = [
        f"version={index.get('version')} git={index.get('git_rev', '?')} generated={index.get('generated_at', '?')}",
        "entrypoints:",
    ]
    for ep in index.get("entrypoints", [])[:12]:
        port = f":{ep['port']}" if ep.get("port") else ""
        lines.append(f"  - {ep.get('id')}: {ep.get('path')}{port}")
    lines.append("modules:")
    for mod in index.get("modules", [])[:12]:
        lines.append(f"  - {mod.get('path')}: {mod.get('role')}")
    flows = index.get("flows", [])
    if flows:
        lines.append("flows:")
        for flow in flows[:3]:
            lines.append(f"  - {flow.get('name')}: {' → '.join(flow.get('steps', [])[:4])}")
    quick_facts = index.get("quick_facts", [])
    if quick_facts:
        lines.append("quick_facts:")
        for fact in quick_facts[:12]:
            lines.append(f"  - {fact}")
    env_keys = index.get("env_keys", [])
    if env_keys:
        lines.append(f"env_keys: {', '.join(str(k) for k in env_keys[:12])}")
    legacy = index.get("legacy_deny_paths", [])
    if legacy:
        lines.append("legacy_not_production: " + ", ".join(legacy[:8]))
    return "\n".join(lines)


def _match_glob(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    pat = pattern.replace("\\", "/").lstrip("/")
    if "**" in pat:
        return fnmatch.fnmatch(normalized, pat) or fnmatch.fnmatch(
            f"/{normalized}", pat.replace("**", "*")
        )
    return fnmatch.fnmatch(normalized, pat) or fnmatch.fnmatch(f"/{normalized}", pat)


def is_path_allowed(path: str, mode: str = "lean") -> bool:
    if mode == "explore":
        for sub in _DEFAULT_DENY_SUBSTRINGS:
            if sub in path:
                return False
        return True

    index = load_index()
    normalized = path.replace("\\", "/")
    for deny in index.get("deny_globs", []):
        if _match_glob(normalized, deny):
            return False
    for sub in _DEFAULT_DENY_SUBSTRINGS:
        if sub in normalized:
            return False
    for legacy in index.get("legacy_deny_paths", []):
        if legacy in normalized:
            return False

    for pattern in index.get("read_allowlist", []):
        if _match_glob(normalized, pattern):
            return True
    return False


def build_system_prefix(mode: str = "lean", explore_mode: bool = False) -> str:
    effective_mode = "explore" if explore_mode or mode == "explore" else "lean"
    index = load_index()
    max_chars = _env_int("OPRAI_CONTEXT_MAX_CHARS", 6000)

    if effective_mode == "lean":
        rules = (
            "Rules: answer from map/index first; max 3 file reads; "
            "no shell; only read paths from read_allowlist; "
            "if insufficient data ask operator — do not scan repo."
        )
    else:
        rules = (
            "Rules: explore mode — broader read/shell allowed but "
            "never modify files without autonomy arm; never read secrets."
        )

    armed_banner = ""
    if explore_mode and os.getenv("OPRAI_ALLOW_WRITES", "").strip().lower() in ("1", "true", "yes"):
        armed_banner = (
            "Autonomy armed: Write allowed under active cli.explore.json permissions "
            "(typically oprai_lab/**). Use Write tool for deliverables."
        )

    summary = _compact_index_summary(index)
    chunks_body = ["--- index summary ---", summary]
    if _env_bool("OPRAI_CONTEXT_INCLUDE_MAP", False):
        map_budget = min(2000, max(400, max_chars - len(summary) - 500))
        map_excerpt = load_map_excerpt(map_budget)
        chunks_body.extend(["", "--- map excerpt ---", map_excerpt])

    research_excerpt = load_research_excerpt()
    research_block: List[str] = []
    if research_excerpt:
        research_block = [
            "[OPRAI_RESEARCH — SECONDARY SIGNALS ONLY]",
            "Primary authority: TASK + repo as-built. Do not implement from web alone.",
            "Every external idea must appear in report as ADOPTED/REJECTED/DEFER with rationale.",
            "",
            research_excerpt,
            "--- end OPRAI_RESEARCH ---",
            "",
        ]

    prefix = "\n".join(
        [
            f"[OPRAI_CONTEXT mode={effective_mode}]",
            rules,
            *([armed_banner] if armed_banner else []),
            f"Index version: {index.get('version')} generated: {index.get('generated_at')}",
            "",
            *chunks_body,
            "--- end OPRAI_CONTEXT ---",
            "",
        ]
    )
    if len(prefix) > max_chars:
        prefix = prefix[: max_chars - 20].rstrip() + "\n[...truncated]"
    if research_block:
        prefix = prefix + "\n".join(research_block)
    return prefix


def get_status() -> Dict[str, Any]:
    index = load_index()
    map_exists = MAP_PATH.is_file()
    map_mtime = None
    if map_exists:
        map_mtime = datetime.fromtimestamp(MAP_PATH.stat().st_mtime, tz=timezone.utc).isoformat()

    prefix_sample = ""
    if _env_bool("OPRAI_CONTEXT_ENABLED", True):
        prefix_sample = build_system_prefix(mode=os.getenv("OPRAI_CLI_PROFILE", "lean"))
    research_path = os.getenv("OPRAI_RESEARCH_ARTIFACT", "").strip()
    research_chars = len(load_research_excerpt()) if research_path else 0
    research_metrics = load_research_metrics()
    return {
        "enabled": _env_bool("OPRAI_CONTEXT_ENABLED", True),
        "max_chars": _env_int("OPRAI_CONTEXT_MAX_CHARS", 6000),
        "research_artifact": research_path or None,
        "research_char_count": research_chars,
        "research_max_chars": _env_int("OPRAI_RESEARCH_MAX_CHARS", 4000),
        "research_metrics": research_metrics,
        "cli_profile": os.getenv("OPRAI_CLI_PROFILE", "lean"),
        "explore_allowed": _env_bool("OPRAI_EXPLORE_ALLOWED", False),
        "index_path": str(INDEX_PATH),
        "index_version": index.get("version"),
        "index_generated_at": index.get("generated_at"),
        "index_git_rev": index.get("git_rev"),
        "map_path": str(MAP_PATH),
        "map_mtime": map_mtime,
        "prefix_char_count": len(prefix_sample),
        "read_allowlist_count": len(index.get("read_allowlist", [])),
    }


def context_enabled() -> bool:
    return _env_bool("OPRAI_CONTEXT_ENABLED", True)


def resolve_cli_profile(explore_mode: bool = False) -> str:
    if explore_mode or os.getenv("OPRAI_EXPLORE_MODE", "").strip() in ("1", "true", "yes"):
        return "explore"
    return os.getenv("OPRAI_CLI_PROFILE", "lean").strip() or "lean"


def chat_max_tokens() -> int:
    return _env_int("OPRAI_CHAT_MAX_TOKENS", 4096)


def explore_allowed() -> bool:
    return _env_bool("OPRAI_EXPLORE_ALLOWED", False)


def auto_plan_detect_enabled() -> bool:
    return _env_bool("OPRAI_AUTO_PLAN_DETECT", False)

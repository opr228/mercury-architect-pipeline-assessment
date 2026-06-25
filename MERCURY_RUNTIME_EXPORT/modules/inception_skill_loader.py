"""Load matching Cursor skills into Mercury agent system prompt."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

# Always injected for agent loop (core layer).
_ALWAYS_ON_SKILLS: Tuple[str, ...] = ("dllm-mercury", "oprai-core")

# Domain skills matched by keywords (most specific wins).
_DOMAIN_SKILL_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("mercury-code-review", ("phase=review", "code review", " p0", " p1", "severity")),
    ("mercury-verify", ("phase=verify", "verify.json", "acceptance criteria")),
    ("mercury-tdd", ("phase=implement", "tdd", "red green", "per plan_")),
    ("mercury-architect", ("phase=design", "adr", "architecture decision", "trade-off")),
    ("mercury-roadmap", ("roadmap", "gap analysis", "phase 0", "phase 1", "migration plan", "mercury2 vs cursor")),
    ("mercury-ecosystem-audit", ("audit", "ecosystem", "codebase_map")),
    ("mercury-consult", ("consult", "architecture", "prompt engineering")),
    ("mercury-implement-stamp", ("stamp", "implement", "task/", "=== task")),
)


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, parts[2].strip()


def _skill_roots(workspace: str) -> List[Path]:
    roots: List[Path] = []
    ws = Path(workspace).resolve()
    roots.append(ws / ".cursor" / "skills")
    lab = Path(os.getenv("OPRAI_LAB_ROOT", str(ws / "oprai_lab") if ws.name != "oprai_lab" else ws))
    if lab.resolve() != ws:
        roots.append(lab / ".cursor" / "skills")
    prod = Path("/home/opr/.cursor/skills")
    if prod not in roots and prod.is_dir():
        roots.append(prod)
    return roots


def _collect_skills(workspace: str) -> List[Tuple[str, str, str]]:
    """Return list of (name, description, body)."""
    found: List[Tuple[str, str, str]] = []
    seen: set[str] = set()
    for root in _skill_roots(workspace):
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                text = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            name = meta.get("name") or skill_md.parent.name
            if name in seen:
                continue
            seen.add(name)
            desc = meta.get("description") or ""
            found.append((name, desc, body))
    return found


def _skill_by_name(skills: List[Tuple[str, str, str]], name: str) -> Optional[str]:
    for n, _, body in skills:
        if n == name:
            return body
    return None


def _truncate_snippet(header: str, body: str, max_chars: int) -> str:
    snippet = header + body
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max_chars - 20] + "\n[...truncated]"


def _load_core_skills(skills: List[Tuple[str, str, str]]) -> str:
    max_chars = int(os.getenv("INCEPTION_CORE_SKILL_MAX_CHARS", "1200"))
    parts: List[str] = []
    per_skill = max(200, max_chars // max(1, len(_ALWAYS_ON_SKILLS)))
    for name in _ALWAYS_ON_SKILLS:
        body = _skill_by_name(skills, name)
        if not body:
            continue
        parts.append(_truncate_snippet(f"# Core skill: {name}\n\n", body, per_skill))
    combined = "\n\n".join(parts)
    if len(combined) > max_chars:
        combined = combined[: max_chars - 20] + "\n[...truncated]"
    return combined


def _score_domain_skill(name: str, description: str, user_text: str) -> int:
    lowered = (user_text or "").lower()
    if name in _ALWAYS_ON_SKILLS:
        return 0
    score = 0
    # Phase= prefix gives strong signal for architect pipeline skills
    phase_match = re.search(r"phase\s*[=:]\s*(\w+)", lowered)
    if phase_match:
        phase_kw = f"phase={phase_match.group(1).lower()}"
        for skill_name, keywords in _DOMAIN_SKILL_KEYWORDS:
            if skill_name == name and phase_kw in [k.lower() for k in keywords]:
                score += 10
    for skill_name, keywords in _DOMAIN_SKILL_KEYWORDS:
        if skill_name == name:
            score += 5
        for kw in keywords:
            if kw.lower() in lowered:
                score += 3
    for token in re.findall(r"[a-z0-9_./-]+", description.lower()):
        if len(token) > 3 and token in lowered:
            score += 1
    if name.lower() in lowered:
        score += 4
    return score


def _load_domain_skill(skills: List[Tuple[str, str, str]], user_text: str) -> Optional[str]:
    max_chars = int(os.getenv("INCEPTION_DOMAIN_SKILL_MAX_CHARS", "2000"))
    ranked = sorted(
        ((_score_domain_skill(n, d, user_text), n, b) for n, d, b in skills if n not in _ALWAYS_ON_SKILLS),
        reverse=True,
    )
    if not ranked or ranked[0][0] < 3:
        return None
    _, best_name, best_body = ranked[0]
    return _truncate_snippet(f"# Domain skill: {best_name}\n\n", best_body, max_chars)


def load_matching_skill(workspace: str, user_text: str) -> Optional[str]:
    """Return core (always-on) + best domain skill snippet for the agent loop."""
    skills = _collect_skills(workspace)
    if not skills:
        return None
    parts: List[str] = []
    core = _load_core_skills(skills)
    if core:
        parts.append(core)
    domain = _load_domain_skill(skills, user_text)
    if domain:
        parts.append(domain)
    if not parts:
        return None
    combined = "\n\n".join(parts)
    total_max = int(os.getenv("INCEPTION_SKILL_MAX_CHARS", "3200"))
    if len(combined) > total_max:
        combined = combined[: total_max - 20] + "\n[...truncated]"
    return combined


# Backward-compatible alias for tests scoring domain keywords only.
_SKILL_KEYWORDS = _DOMAIN_SKILL_KEYWORDS


def _score_skill(name: str, description: str, user_text: str) -> int:
    return _score_domain_skill(name, description, user_text)

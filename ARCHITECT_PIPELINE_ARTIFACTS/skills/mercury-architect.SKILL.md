---
name: mercury-architect
description: ADR and architecture design — trade-offs, interfaces, verified/unverified claims. Use for Phase=DESIGN, ADR, architecture decisions without code.
layer: domain
---

# mercury-architect — design before code

**Core:** `dllm-mercury` + `oprai-core` (always-on).

## Triggers

`Phase=DESIGN`, `ADR`, `architecture decision`, `trade-off`, `ARCHITECTURE`

## Workflow

1. **Read once:** `docs/CODEBASE_MAP.md`, target module(s), `grep_search` call sites
2. **Output:** ADR in chat (consult) OR `write_file` when `deliverable=.../ADR_*.md`
3. **Sections:** Context | Decision | Consequences | Trade-offs table (Verified? column) | Verified files
4. **No code** until operator approves design

## ADR template

```markdown
# ADR-{N}: {title}
## Status: proposed
## Context
## Decision
## Consequences
## Trade-offs
| Option | Pros | Cons | Verified? |
## Verified files
```

## Anti-patterns

- Implementation code in DESIGN phase
- Unverified claims without `Verified? ❌`
- Phantom file writes in consult mode

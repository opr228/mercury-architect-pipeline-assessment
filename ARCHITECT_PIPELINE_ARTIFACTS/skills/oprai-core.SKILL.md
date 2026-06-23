---
name: oprai-core
description: Agent harness core rules — lab vs prod, autonomy arm, consult vs deliverable. Always-on for Mercury agent in explore mode.
layer: core
always_on: true
---

# oprai-core — workspace & autonomy

## Instances

| Target | LLM | Writes |
|--------|-----|--------|
| Prod | Mercury only | armed + explore only |
| Lab | Mercury only | lab workspace root |

**Mercury-only:** `LLM_PROVIDER=inception` only. Block fallback CLI when `OPRAI_MERCURY_ONLY=1`.

## Architect pipeline phases

Use `Phase=DESIGN|PLAN|IMPLEMENT|VERIFY|REVIEW` in messages. See `docs/MERCURY_ARCHITECT_PIPELINE.md`.

## Path rules

- Lab paths are relative to lab workspace root — no double nesting.
- Never bulk-copy prod → lab; use lab-target writes only.

## Write path (deliverables)

1. Arm autonomy (TTL + budgets)
2. Explore mode + lab target for lab work
3. Message includes `deliverable=<relative-path>` when a file is required
4. Agent: evidence reads → `write_file` → validator `stub: false`

## Consult vs implement

| Mode | write_file | deliverable= |
|------|------------|--------------|
| CONSULT / ARCHITECTURE | **blocked** | omit |
| Audit / roadmap / implement | allowed when armed | include path |

## Deny (unless explicitly tasked)

- Secret env files
- System service configs
- Legacy orchestrator versions

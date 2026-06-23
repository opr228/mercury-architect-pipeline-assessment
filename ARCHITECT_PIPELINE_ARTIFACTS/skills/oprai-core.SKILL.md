---
name: oprai-core
description: OPRAI core operator rules — lab vs prod, lab_target paths, autonomy arm, consult vs deliverable, P-15. Always-on for Mercury agent in explore mode.
layer: core
always_on: true
---

# oprai-core — OPRAI workspace & autonomy

## Instances

| Target | API | LLM | Writes land in |
|--------|-----|-----|----------------|
| Prod | `:5004` | Mercury only | `/home/opr` (armed + explore only) |
| Lab | `:20004` or `lab_target: true` | Mercury only | `/home/opr/oprai_lab` |

**Mercury-only:** OPRAI uses `LLM_PROVIDER=inception` only. Never route to Cursor CLI (`OPRAI_MERCURY_ONLY=1`).

## Architect pipeline phases

Use `Phase=DESIGN|PLAN|IMPLEMENT|VERIFY|REVIEW` in messages. See `docs/MERCURY_ARCHITECT_PIPELINE.md`.

## Path rules (P-15)

- With `lab_target: true`, workspace is lab root — paths **without** `oprai_lab/` prefix.
- Correct: `task_history/oprai_improve_lab/results/REPORT.md`
- Wrong: `oprai_lab/task_history/...` (double nesting)
- Never bulk rsync prod → lab; use `lab_target` writes only.

## Write path (all required for deliverables)

1. `POST /api/autonomy/arm` (TTL + budgets)
2. Chat: `explore_mode: true`, `lab_target: true` for lab work
3. Message includes `deliverable=<relative-path>` when a file is required
4. Agent: evidence reads → `write_file` → validator `stub: false`

## Consult vs implement

| Mode | write_file | deliverable= |
|------|------------|--------------|
| CONSULT / ARCHITECTURE (no deliverable) | **blocked** | omit |
| Audit / roadmap / implement | allowed when armed | include path |

## Deny (never modify unless explicitly tasked)

- `env.local`, `.env`, secrets
- `/etc/systemd/`, `.service` files
- Legacy orchestrators v2–v7

## Target flags (one per request)

- `lab_target: true` — lab workspace
- `remote_target: true` + `project_id` — remote staging
- Do **not** combine `lab_target` + `remote_target`

## Evidence after lab tasks

- Append to `task_history/oprai_improve_lab/results/change_ledger.jsonl` when applicable
- Check `logs/agent_activity.log` for `[tool]` / `[change]` / `write_file`

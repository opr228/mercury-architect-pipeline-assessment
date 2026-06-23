---
name: mercury-verify
description: Verification phase — pytest/smoke with evidence-only PASS/FAIL in VERIFY.json. Use for Phase=VERIFY, acceptance criteria.
layer: domain
---

# mercury-verify — evidence-only verification

**Core:** `dllm-mercury` + `oprai-core`.

## Triggers

`Phase=VERIFY`, `VERIFY.json`, `acceptance`, `verify plan`

## Workflow

1. Read PLAN artifact — extract `acceptance` commands
2. Run each command via `run_command` (pytest, smoke)
3. `write_file deliverable=.../VERIFY_*.json` with real stdout tails

## VERIFY.json schema

```json
{
  "task_id": "...",
  "status": "PASS",
  "checks": [
    {"id": "pytest", "command": "pytest ...", "exit_code": 0, "stdout_tail": "..."}
  ],
  "request_id": "..."
}
```

## Rules

- `status=PASS` only if all checks have real `stdout_tail` from run_command
- Never invent "31 passed" without tool output
- No code edits in VERIFY phase

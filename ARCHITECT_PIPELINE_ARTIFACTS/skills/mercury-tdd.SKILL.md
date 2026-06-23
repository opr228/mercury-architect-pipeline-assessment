---
name: mercury-tdd
description: TDD implement — red/green/refactor, minimal diff, read-before-edit. Use for Phase=IMPLEMENT, TDD, armed writes per PLAN.
layer: domain
---

# mercury-tdd — test-driven implement

**Core:** `dllm-mercury` + `oprai-core`.

## Triggers

`Phase=IMPLEMENT`, `TDD`, `red green`, `implement stamp`, `per PLAN_`

## Prerequisites

- `/api/autonomy/arm` + `explore_mode` + `lab_target` for lab work
- Valid PLAN artifact on disk (`plan_path=` or `per PLAN_*.md`)

## Workflow (strict order)

1. Read PLAN frontmatter — `scope_files`, `max_diff_lines`, `acceptance`
2. Read each scope file once
3. **Red:** write/edit test → `run_command pytest ...` → expect FAIL (record exit != 0)
4. **Green:** minimal `edit_file` implementation → `pytest` → expect PASS (exit 0)
5. Stamp in chat: request_id, files, verify command result

## Rules

- Minimal diff — stay within PLAN `max_diff_lines`
- Read surrounding code before edit
- Never claim PASS without pytest stdout in tool result

## Anti-patterns

- Implement before test
- Skip pytest
- Diff beyond PLAN budget

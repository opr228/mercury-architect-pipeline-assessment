---
name: mercury-code-review
description: Code review against PLAN/ADR — severity P0-P3, consult-only. Use for Phase=REVIEW, diff review.
layer: domain
---

# mercury-code-review — review against plan

**Core:** `dllm-mercury` + `oprai-core`.

## Triggers

`Phase=REVIEW`, `code review`, `P0`, `P1`, severity

## Workflow

1. Read PLAN + ADR (if exists)
2. Read changed files from PLAN `scope_files`
3. Chat review only — **no write_file**

## Output format

```markdown
## Review summary
| Severity | Issue | File | Evidence |
| P0 | ... | modules/foo.py | line N |
## Scope compliance
- In scope: ...
- Out of scope respected: yes/no
## Verdict: APPROVE | CHANGES_REQUESTED
```

## Severity

- **P0:** breaks acceptance / security / data loss
- **P1:** wrong behavior, missing tests
- **P2:** style, minor refactor
- **P3:** nit

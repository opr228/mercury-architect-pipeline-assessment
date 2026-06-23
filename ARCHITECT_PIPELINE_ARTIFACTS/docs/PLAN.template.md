---
task_id: {task-id}
scope_files:
  - modules/example.py
  - tests/unit/test_example.py
out_of_scope:
  - modules/orchestrator_api_core.py
acceptance:
  - pytest tests/unit/test_example.py -q
  - no fabrication in deliverable
phases:
  - id: implement
    max_diff_lines: 120
---

# Implementation plan: {title}

## Scope
Files and behavior in scope.

## Out of scope
Explicit exclusions.

## Acceptance criteria
Commands that must pass in VERIFY phase.

## Implementation phases
Step-by-step with risks.

## Risks
What could go wrong.

## Verified files
- docs/CODEBASE_MAP.md

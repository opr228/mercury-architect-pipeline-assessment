# Mercury Architect Pipeline

OPRAI production intelligence runs **Mercury-only** (`LLM_PROVIDER=inception`, `OPRAI_MERCURY_ONLY=1`). Cursor CLI is legacy and blocked at runtime.

## Phases

| Phase | Message prefix | Skill | Writes |
|-------|----------------|-------|--------|
| RECON | `Phase=RECON` | mercury-ecosystem-audit | optional |
| DESIGN | `Phase=DESIGN` | mercury-architect | consult-only |
| PLAN | `Phase=PLAN` | mercury-roadmap | PLAN.md |
| IMPLEMENT | `Phase=IMPLEMENT` | mercury-tdd | code (armed) |
| VERIFY | `Phase=VERIFY` | mercury-verify | VERIFY.json |
| REVIEW | `Phase=REVIEW` | mercury-code-review | chat only |

## Operator happy path

```
1. POST /api/chat  explore  lab_target
   "Phase=DESIGN ARCHITECTURE: <task>"

2. Operator approves design

3. POST /api/chat  deliverable=task_history/.../PLAN_<task>.md
   "Phase=PLAN implementation plan for <task>"

4. POST /api/autonomy/arm

5. POST /api/chat  armed  explore  lab_target
   "Phase=IMPLEMENT per PLAN_<task>.md TDD"

6. POST /api/chat  explore  lab_target
   "Phase=VERIFY per PLAN_<task>.md deliverable=.../VERIFY_<task>.json"

7. POST /api/chat  "Phase=REVIEW per PLAN_<task>.md"
```

## PLAN frontmatter (required)

See [docs/templates/PLAN.template.md](templates/PLAN.template.md).

## TaskRunner

```python
from modules.task_runner import TaskRunner, TaskSpec

runner = TaskRunner("/home/opr/oprai_lab")
results = runner.run_pipeline([...])
```

Checkpoints: `task_history/oprai_improve_lab/results/{task_id}_checkpoint.json`

## Gates

- **Mercury-only:** `llm_router` rejects non-inception providers
- **Consult:** DESIGN/REVIEW block writes without `deliverable=`
- **Arm:** IMPLEMENT requires `/api/autonomy/arm`
- **Plan:** PLAN.md must pass `plan_validator`
- **TDD:** IMPLEMENT requires pytest FAIL then PASS (`OPRAI_TDD_GATE=1`)
- **Diff budget:** `edit_file` enforces PLAN `max_diff_lines`
- **Verify:** VERIFY.json PASS requires real `stdout_tail`

## Anti-patterns

- Single long chat for audit + implement
- Mixing CONSULT and IMPLEMENT in one message
- Invented pytest output in VERIFY.json
- Auto-promote lab to prod (P-15)

## Golden path

```bash
python3 /home/opr/oprai_lab/scripts/run_architect_pipeline_golden.py
```

# OPRAI Mercury Operations

Operations runbook for Inception Mercury 2 on OPRAI (viberbot).

## Mercury-only policy

Production path is **Mercury only** (`OPRAI_MERCURY_ONLY=1` default). `llm_router` blocks `cursor_cli` and other providers. Emergency override: `OPRAI_MERCURY_ONLY=0`.

See [MERCURY_ARCHITECT_PIPELINE.md](MERCURY_ARCHITECT_PIPELINE.md) for phase workflow (`Phase=DESIGN|PLAN|IMPLEMENT|VERIFY|REVIEW`).

## Architecture as-built

```text
POST /api/chat (:5004 prod, :20004 lab)
  → orchestrator_api.call_orchestrator
  → modules.llm_router.complete (LLM_PROVIDER=inception, mercury-only gate)
  → modules.inception_adapter.call_agent
      → inception_agent_policy (phase gates, consult, deliverable)
      → inception_skill_loader (core + domain skills)
      → inception_agent_tools (read/grep/write/edit/...)
  → Mercury API /v1/chat/completions
```

Optional: `modules/task_runner.py` for multi-phase pipelines with checkpoints.

## How to start

```bash
source /home/opr/env.local   # LLM_PROVIDER=inception
systemctl restart orchestrator-api.service
curl -s http://127.0.0.1:5004/api/health | python3 -m json.tool
bash /home/opr/oprai_lab/scripts/smoke_inception_agent.sh
python3 /home/opr/oprai_lab/scripts/run_architect_pipeline_golden.py
```

Lab API: port `20004` with `OPRAI_LAB_MODE=1` or lab orchestrator instance.

## Gates

- **propose (default):** read-only; no writes.
- **armed + explore_mode:** writes allowed unless consult-only.
- **Phase=DESIGN/REVIEW:** consult-only without `deliverable=`.
- **Phase=IMPLEMENT:** requires arm + valid PLAN + TDD gate (`OPRAI_TDD_GATE=1`).
- **deliverable gate:** fail-closed until `write_file` + validator pass.
- **plan_validator:** PLAN.md requires YAML frontmatter.

## Skills (Mercury)

| Skill | Trigger |
|-------|---------|
| dllm-mercury, oprai-core | always-on |
| mercury-architect | Phase=DESIGN, ADR |
| mercury-roadmap | Phase=PLAN, roadmap |
| mercury-tdd | Phase=IMPLEMENT, TDD |
| mercury-verify | Phase=VERIFY |
| mercury-code-review | Phase=REVIEW |
| mercury-ecosystem-audit | audit, ecosystem |
| mercury-consult | CONSULT |
| mercury-implement-stamp | STAMP, IMPLEMENT |

Located in `.cursor/skills/` (prod + lab mirror).

## Verification

```bash
python3 -m pytest tests/unit/test_inception*.py tests/unit/test_plan_validator.py \
  tests/unit/test_task_phase.py tests/unit/test_task_runner.py \
  tests/unit/test_mercury_only_router.py tests/unit/test_tdd_gate.py \
  tests/unit/test_diff_budget.py tests/unit/test_codebase_context_cache.py -q
bash /home/opr/oprai_lab/scripts/smoke_inception_agent.sh
```

## Cursor CLI (archived)

Legacy code remains in `modules/cursor_cli_adapter.py` but is **blocked** when `OPRAI_MERCURY_ONLY=1`. Do not use for production OPRAI tasks.

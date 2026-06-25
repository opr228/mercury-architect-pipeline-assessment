# Mercury Runtime Export

This folder contains a curated public export of OPRAI Mercury runtime components.

## Scope

- API entrypoints for Mercury-driven orchestration
- Core Mercury adapter/policy/tool modules
- Router and telemetry integration
- Unit tests relevant to Mercury behavior
- Operational docs and smoke scripts

## Included paths

- `orchestrator_api.py`
- `agent_orchestrator_v8.py`
- `oprai_lab/ork_orchestrator_api.py`
- `oprai_lab/ork_agent_orchestrator_v8.py`
- `modules/inception_adapter.py`
- `modules/inception_agent_tools.py`
- `modules/inception_agent_policy.py`
- `modules/inception_skill_loader.py`
- `modules/inception_thread_compat.py`
- `modules/llm_router.py`
- `modules/llm_trace.py`
- `modules/orchestrator_api_core.py`
- `modules/codebase_context.py`
- `modules/autonomy_controller.py`
- `tests/unit/test_inception_agent_policy.py`
- `tests/unit/test_llm_trace_usage.py`
- `docs/OPRAI_MERCURY_OPERATIONS.md`
- `docs/CURSOR_CLI_RUNBOOK.md`
- `scripts/smoke_inception_agent.sh`
- `scripts/smoke_inception_mercury.sh`
- `env.inception.mercury.example`

## Security and publishing notes

- No secret files are included (`env.local`, `env.secrets`, `.env`, `env.cursor.secret` are excluded).
- Example docs may reference local filesystem paths from the source environment; treat them as placeholders.
- Use `env.inception.mercury.example` as the template for configuration.

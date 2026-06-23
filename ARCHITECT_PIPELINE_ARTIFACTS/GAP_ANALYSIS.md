# GAP_ANALYSIS — roadmap v4 read-loop и план фикса

## Симптом

`run_roadmap_v4_architect.py` → `agent exceeded max steps (40)`, deliverable не создан.  
`llm_trace`: 40 API calls, все `completion_tokens=0`, `synthesis_step=false`.

## Root cause (подтверждено activity log)

Файл: `logs/agent_activity_roadmap-v4-arch-36437f91.log`

| # | Наблюдение |
|---|------------|
| 1 | **Ни одного `write_file`** за 40 steps |
| 2 | **Циклические re-read** одних и тех же 7 paths |
| 3 | Порядок tools **не совпадает** с ordered prompt (trace snippet на step 2, до llm_router) |
| 4 | `inception_agent_tools.py` читается, но loop продолжается |
| 5 | Gates **не форсируют** write после mandatory reads — dLLM снова выбирает read |

### Паттерн loop (повторяется ~5× за run)

```
CODEBASE_MAP → trace_snippet → llm_router → inception_adapter →
inception_agent_policy → cursor_cli_adapter → (repeat)
```

### Почему v3 иногда проходил

- Pre-extracted trace snippet (1 read)
- Ordered prompt в user message
- `INCEPTION_FORCE_AUDIT_TOOL=1`, `MAX_NUDGES=4`
- **Без** `Phase=PLAN` → меньше phase mandatory reads + plan_validator confusion

### Почему pipeline infrastructure не виноват

Skills inject ✓, Mercury-only ✓, fail-closed ✓. Проблема: **dLLM harness не переводит агента в synthesis/write** после evidence.

---

## Фиксы по файлам (приоритет)

### P0 — unblock roadmap E2E

| # | Файл | Изменение |
|---|------|-----------|
| 1 | `scripts/run_roadmap_v4_architect.py` + `task_runner.py` | Pre-step: `build_evidence_bundle.py` → inject bundle path as **single read** (step 1), step 2 = `write_file` only |
| 2 | `inception_agent_policy.py` | `task_class ROADMAP` distinct from PLAN; skip plan_validator; after all mandatory reads done → nudge **only** `write_file` |
| 3 | `inception_tool_helpers.py` | For roadmap/audit: `read_file` identical path **2nd time** → block with error (threshold=1 for deliverable tasks) |
| 4 | `inception_adapter.py` | When `mandatory_evidence_pending` returns empty AND deliverable pending → force next tool to `write_file` (soft required, step-scoped) |

### P1 — dLLM split-run

| # | Файл | Изменение |
|---|------|-----------|
| 5 | `task_runner.py` | `ROADMAP mode`: Run A RECON (reads, max 8 steps) → Run B WRITE (bundle in system, max 3 steps) |
| 6 | `build_evidence_bundle.py` | Auto-include trace snippet via `extract_llm_trace_metrics.py --last 20` |

### P2 — verify + regression

| # | Файл | Изменение |
|---|------|-----------|
| 7 | `tests/unit/test_inception_agent_policy_gates.py` | Test: after reads complete, re-read same path blocked |
| 8 | `tests/unit/test_task_runner.py` | Test: ROADMAP deliverable uses IMPLEMENT validator, not plan_validator |
| 9 | `run_roadmap_v4_architect.py` | PASS criteria: file exists, ≥80 lines, no fabrication |

---

## Verify commands (after fix)

```bash
cd /home/opr
python3 -m pytest tests/unit/test_inception_agent_policy_gates.py \
  tests/unit/test_task_runner.py tests/unit/test_inception_tool_helpers.py -q
python3 /home/opr/oprai_lab/scripts/run_roadmap_v4_architect.py
```

Expected: `MERCURY2_ROADMAP_V4_VERIFY.json` → `"pass": true`, deliverable ≥80 lines.

---

## Out of scope (не чинить в этом цикле)

- Cursor CLI fallback
- Auto-promote lab→prod (P-15)
- Mercury API / model weights

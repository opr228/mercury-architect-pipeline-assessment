# Mercury Architect Pipeline — полный пакет артефактов

Собрано: 2026-06-23  

Публичный репозиторий: https://github.com/opr228/mercury-architect-pipeline-assessment

Архив: `ARCHITECT_PIPELINE_ARTIFACTS.tar.gz` (в корне репозитория)

---

## Структура пакета

```
ARCHITECT_PIPELINE_ARTIFACTS/
├── INDEX.md                          ← этот файл
├── GAP_ANALYSIS.md                   ← root cause + fix map по файлам
├── PIPELINE_PHASES.json              ← budgets фаз (из кода)
├── CONFIG_GATES_TOOLCHOICE.md        ← env + tool_choice + gate logic
├── EXAMPLES_INPUT_OUTPUT.md          ← промпты и expected deliverables
├── modules/                          ← OPRAI adapter + validators + TaskRunner
├── modules/deps/                     ← agent_activity, instance_paths, llm_trace, …
├── scripts/                          ← runners + build_evidence_bundle + extract_llm_trace_metrics
├── tests/unit/                       ← pytest для gates, task_runner, validators
├── docs/                             ← pipeline docs + PLAN template
├── skills/                           ← dllm-mercury, roadmap, architect, …
├── verify/                           ← JSON результаты прогонов
├── examples/                         ← v3 deliverable, trace snippet, briefing
├── logs/                             ← llm_trace + agent_activity + TOOL_CALL_SUMMARY
└── env/                              ← env.inception.mercury.example
```

---

## 1. OPRAI-адаптер и связанные модули

| Файл | Строк | Назначение |
|------|-------|------------|
| `modules/inception_adapter.py` | ~1140 | Agent loop, synthesis/tool steps, gates, API |
| `modules/inception_agent_policy.py` | ~790 | Phase policy, mandatory reads, PHASE_BUDGETS |
| `modules/inception_agent_tools.py` | ~501 | 7 tools, TDD/diff budget |
| `modules/inception_tool_helpers.py` | ~131 | Loop detector, arg recovery |
| `modules/inception_skill_loader.py` | ~168 | Always-on skills |
| `modules/task_runner.py` | ~138 | Phase state machine |
| `modules/plan_validator.py` | ~121 | PLAN.md YAML frontmatter |
| `modules/deliverable_validator.py` | ~338 | Stub + fabrication + VERIFY schema |
| `modules/llm_router.py` | ~347 | Mercury-only routing |

**Примечание:** отдельного `fabrication_verifier.py` нет — логика в `deliverable_validator.py` (`check_fabrication_markers`).

---

## 2. TaskRunner и фазы

Код: `modules/task_runner.py`  
Отдельного `pipeline.yaml` **нет** — фазы в `inception_agent_policy.PHASE_BUDGETS` → `PIPELINE_PHASES.json`.

Документация: `docs/MERCURY_ARCHITECT_PIPELINE.md`  
Golden path: `scripts/run_architect_pipeline_golden.py` → `verify/ARCHITECT_PIPELINE_GOLDEN.json`

---

## 3. Evidence bundle

`scripts/build_evidence_bundle.py` — pre-materialize файлов в один markdown.

Пример v3 snippet (ручной аналог): `examples/_ROADMAP_v3_trace_snippet.md`

**Статус:** bundle **не wired** в TaskRunner автоматически (следующая доработка).

---

## 4. Validators

| Модуль | Схема |
|--------|-------|
| `plan_validator.py` | YAML: `task_id`, `scope_files`, `acceptance`, `phases`, body ≥20 lines |
| `deliverable_validator.py` | `.md` min lines, fabrication patterns, VERIFY.json schema |
| `docs/PLAN.template.md` | Шаблон PLAN frontmatter |

Fabrication patterns: `req_2026*`, `req_YYYYMMDD_NNN`, `tokens_input`, `tokens_output`, Fortune-500 marketing.

---

## 5. Логи неудачных запусков (read-loop)

| Файл | Содержание |
|------|------------|
| `GAP_ANALYSIS.md` | Root cause + приоритетный fix map |
| `logs/TOOL_CALL_SUMMARY.md` | 40 reads, 0 write_file, частота по path |
| `logs/agent_activity_roadmap-v4-arch-36437f91.log` | **Пошаговые tool calls** (read_file paths) |
| `verify/MERCURY2_ROADMAP_V4_VERIFY.json` | FAIL: 40 tool steps, no deliverable |
| `verify/MERCURY2_ROADMAP_V3_VERIFY.json` | FAIL (повтор): plan_validator на ROADMAP |
| `logs/llm_trace_roadmap-v4-arch-36437f91.jsonl` | 40 API calls, все `completion_tokens=0`, `synthesis_step=false` |
| `logs/llm_trace_roadmap-v4-arch-5f423ee5.jsonl` | Второй v4 run (28 строк trace) |
| `verify/MERCURY_V6_VERIFY.json` | Root-cause note: gate budget vs nudges |

## 8. Tests + deps (добавлено v2)

| Путь | Назначение |
|------|------------|
| `tests/unit/test_inception_agent_policy_gates.py` | Gate logic |
| `tests/unit/test_task_runner.py` | Phase runner |
| `tests/unit/test_inception_tool_helpers.py` | Loop detector |
| `tests/unit/test_plan_validator.py` | PLAN schema |
| + 8 related unit tests | mercury-only, roadmap skill, evidence state, … |
| `modules/deps/agent_activity.py` | Tool call logging |
| `modules/deps/instance_paths.py` | Path resolution |
| `scripts/extract_llm_trace_metrics.py` | Trace metrics for §5 |

---

## 6. Примеры входов / deliverables

См. `EXAMPLES_INPUT_OUTPUT.md`

| Deliverable | Статус |
|-------------|--------|
| `examples/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v3.md` | Есть (135 строк) |
| `MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v4.md` | **Не создан** (fail) |

---

## 7. Конфигурация gates / tool_choice

См. `CONFIG_GATES_TOOLCHOICE.md` и `env/env.inception.mercury.example`

**Важно:** переменная `OPRAI_MAX_GATE_TURNS` **не используется**. Канон: `INCEPTION_MAX_GATE_TURNS`.

---

## Быстрый reproduce

```bash
# v4 architect pipeline (requires full harness checkout + API key)
python3 scripts/run_roadmap_v4_architect.py

# evidence bundle
python3 scripts/build_evidence_bundle.py \
  --task roadmap-v4 \
  --files docs/CODEBASE_MAP.md modules/llm_router.py \
  --workspace . \
  --out results/_bundle.md

# golden path (cache task)
python3 scripts/run_architect_pipeline_golden.py
```

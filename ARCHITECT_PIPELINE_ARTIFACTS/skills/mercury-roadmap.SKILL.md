---
name: mercury-roadmap
description: Evidence-based Mercury 2 vs Cursor CLI roadmap, gap analysis, Phase 0/1/2 plans. Use for roadmap, migration plan, architecture proposal, gap analysis, deliverable=.md plans.
layer: domain
---

# mercury-roadmap — accurate roadmaps and architecture plans

**Core rules:** see always-on skills `dllm-mercury` + `oprai-core` (injected automatically).

## Triggers

`roadmap`, `plan`, `mercury2 vs cursor`, `migration plan`, `phase 0`, `phase 1`, `phase 2`, `gap analysis`, `ROADMAP mode`

## Mandatory workflow (Research → Plan → Verify)

### 1. Research (read each once)

1. `docs/CODEBASE_MAP.md`
2. `modules/llm_router.py`
3. `modules/inception_adapter.py` (limit 200)
4. `modules/inception_agent_policy.py` (limit 200)
5. `modules/inception_agent_tools.py` (limit 100) — copy exact tool names
6. `modules/cursor_cli_adapter.py` (limit 150)
7. Metrics: `run_command` → `python3 scripts/extract_llm_trace_metrics.py --last 20` **or** `read_file logs/llm_trace.jsonl` (limit 20)

### 2. Write deliverable (≥80 lines)

```markdown
## 1. Executive summary (Evidence path column)
## 2. Gap analysis (≥12 rows, Evidence column)
## 3. Roadmap — Phase 0 / Phase 1 / Phase 2
## 4. Architecture proposals (verified only or label unverified)
## 5. Metrics — paste extract_llm_trace_metrics output OR "not measured"
## 6. Open questions
## Verified files
```

### 3. Modes

- **CONSULT** (no `deliverable=`): research + chat; no write_file
- **Deliverable**: after research → **write_file once**; do not re-read same paths repeatedly

## Few-shot

**User:** `ROADMAP deliverable=task_history/.../ROADMAP.md`  
**Agent:** 7 reads + extract metrics → write_file full markdown.

## Anti-patterns (roadmap-specific)

- Phase 0 task tying `OPRAI_CURSOR_STREAM_DEBUG` to Mercury
- `modules/diffusion_llm.py` without OPRAI evidence
- Metrics JSON with `req_2026*` or `tokens_input` fields

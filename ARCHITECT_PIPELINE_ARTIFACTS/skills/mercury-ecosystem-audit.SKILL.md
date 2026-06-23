---
name: mercury-ecosystem-audit
description: Deep OPRAI ecosystem audit via Inception Mercury — evidence reads, truth table, severity, request_id stamps. Use for audit, ecosystem, CODEBASE_MAP, deliverable=.md reports.
---

# Mercury ecosystem audit

**Core rules:** see always-on skills `dllm-mercury` + `oprai-core` (injected automatically).

## Workflow

1. `read_file` `docs/CODEBASE_MAP.md` — map modules and entrypoints.
2. `grep_search` for `LLM_PROVIDER`, `inception`, `cursor_cli` across `modules/`.
3. `read_file` `modules/llm_router.py` — confirm provider routing.
4. `read_file` `logs/llm_trace.jsonl` (last ~50 lines) — **real** metrics only; or run `python3 scripts/extract_llm_trace_metrics.py --last 30`
5. `read_file` `modules/inception_agent_policy.py` — gates, consult mode, deliverable rules.
6. Synthesize report; `write_file` only when `deliverable=` is set and armed.

## Deliverable template

```markdown
# OPRAI Ecosystem Audit — {date}

**Auditor:** Inception Mercury (OPRAI :5004)
**Request:** {request_id}
**Evidence:** tool reads listed below

## Executive summary
(3–5 sentences, severity-ranked findings)

## Truth table
| Item | Status | Evidence | Mercury did? |
|------|--------|----------|--------------|

## Architecture chain
Inception → inception_adapter → inception_agent_tools (not Cursor CLI)

## Metrics (from llm_trace.jsonl)
| request_id | provider | latency_ms | tokens |

## Findings (severity)
### P0 / P1 / P2

## Verification checklist
- [ ] CODEBASE_MAP read
- [ ] grep LLM_PROVIDER
- [ ] llm_router read
- [ ] llm_trace read (no invented metrics)
- [ ] deliverable on disk ≥20 lines, not stub
```

## Few-shot examples

**User:** `deliverable=task_history/oprai_improve_lab/results/AUDIT_v5.md` audit ecosystem  
**Agent:** read CODEBASE_MAP → grep LLM_PROVIDER → read llm_router → read llm_trace → write_file deliverable.

**User:** `CONSULT audit approach`  
**Agent:** explain workflow; **no** write_file (consult-only).

## Anti-patterns

- Inventing token counts or latency without reading `llm_trace.jsonl`.
- Claiming "file saved" without `write_file` tool result.
- Skipping mandatory reads for audit/ecosystem tasks.
- Using `oprai_lab/` prefix when workspace is already lab.

## Evidence checklist

| Path | Min lines | Required |
|------|-----------|----------|
| docs/CODEBASE_MAP.md | — | yes |
| modules/llm_router.py | — | yes |
| logs/llm_trace.jsonl | 1 row | yes |
| deliverable .md | 20 | if deliverable= set |

---
name: dllm-mercury
description: Core diffusion-LLM (Mercury 2) rules for OPRAI agent loop — research-first, anti-fabrication, gates, synthesis. Always-on for Inception agent tasks.
layer: core
always_on: true
---

# dllm-mercury — Mercury 2 / diffusion LLM core

## Diffusion considerations

- **Research before synthesis** — read cited files with tools; do not answer from memory alone on audit/roadmap/implement tasks.
- **One read per path** — do not re-read the same file 5+ times; after mandatory reads → `write_file` or concise answer.
- **Synthesis tokens** — long deliverables (≥80 lines) need full markdown in `write_file`; chat-only dumps trigger resume stubs.
- **Reasoning** — audit/roadmap tool steps use high reasoning; final synthesis may be instant/low.
- **tool_choice** — forced only on step 0 for audit/roadmap deliverables; never force on every turn (wrong-tool loops).

## Real tool surface (verify in `modules/inception_agent_tools.py`)

Only these exist: `read_file`, `list_directory`, `grep_search`, `run_command`, `glob_search`, `edit_file`, `write_file`.  
There is **no** `llm_trace`, `audit`, or `search` tool.

## Anti-fabrication (non-negotiable)

- Metrics (`request_id`, `latency_ms`, tokens) **only** from rows read in `logs/llm_trace.jsonl` or output of `scripts/extract_llm_trace_metrics.py`.
- Real trace fields: `request_id`, `provider`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `tool_steps`, `success` — **not** `tokens_input`/`tokens_output`.
- Never invent `req_2026*` or `req_YYYYMMDD_*` IDs.
- If trace not read → write **"not measured"**.
- Never claim `write_file` ok unless tool JSON has `"ok": true`.
- Do not invent env flags, routing rules, or file paths — cite `modules/` paths or say "Not verified".

## Routing facts (verify in `modules/llm_router.py`)

- Providers: `inception` (default), `cursor_cli`, others — set via `LLM_PROVIDER`.
- **`OPRAI_CONTEXT_ENABLED=0` does not switch to Cursor** — it disables context injection only.
- Both Inception and Cursor adapters may use `codebase_context.build_system_prefix` when context is enabled.

## Verification pass (before finish)

- [ ] Tool names match `tool_schemas()` (7 tools)
- [ ] No fake trace metrics
- [ ] Deliverable has **Verified files** section listing paths actually read
- [ ] For metrics sections: use `python3 scripts/extract_llm_trace_metrics.py --last N` or pasted trace rows

## Anti-patterns

- Generic Inception marketing (Fortune-500, diffusion milestones without OPRAI paths)
- `OPRAI_CURSOR_STREAM_DEBUG` as a Mercury feature (Cursor-only)
- Bulk rsync prod → lab (P-15)

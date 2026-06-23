# Примеры входных сообщений и expected deliverables

## Roadmap v4 (Architect Pipeline — FAIL)

**Script:** `scripts/run_roadmap_v4_architect.py`  
**Expected deliverable:** `task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v4.md`  
**Status:** file NOT created  
**Verify:** `verify/MERCURY2_ROADMAP_V4_VERIFY.json`

### User message (exact template)

```
Phase=PLAN ROADMAP mode — Mercury 2 vs Cursor CLI gap analysis (Phase 0/1/2).

deliverable=task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v4.md

Architecture: Mercury-only OPRAI via Architect Pipeline. Follow mercury-roadmap skill.

Execute tools in order (one each):
1. read_file docs/CODEBASE_MAP.md
2. read_file modules/llm_router.py limit 120
3. read_file modules/inception_adapter.py limit 200
4. read_file modules/inception_agent_policy.py limit 200
5. read_file modules/inception_agent_tools.py limit 100
6. read_file modules/cursor_cli_adapter.py limit 150
7. read_file task_history/oprai_improve_lab/results/_ROADMAP_v4_trace_snippet.md
8. write_file task_history/oprai_improve_lab/results/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v4.md (≥80 lines, sections 1-6 + Verified files)

§5 Metrics: paste step 7 verbatim. Never req_2026* or tokens_input.
```

### Expected deliverable structure (≥80 lines)

```markdown
## 1. Executive summary (Evidence path column)
## 2. Gap analysis (≥12 rows, Evidence column)
## 3. Roadmap — Phase 0 / Phase 1 / Phase 2
## 4. Architecture proposals (verified only or label unverified)
## 5. Metrics — paste extract_llm_trace_metrics output OR "not measured"
## 6. Open questions
## Verified files
```

---

## Roadmap v3 (Stage 1 — historical PASS)

**Script:** `scripts/run_roadmap_v3.py`  
**Deliverable:** `examples/MERCURY2_ROADMAP_vs_CURSOR_CLI_20260623_v3.md`  
**Trace snippet:** `examples/_ROADMAP_v3_trace_snippet.md`

### User message (diff vs v4)

- No `Phase=PLAN` prefix (plain `ROADMAP mode`)
- Same 8-step ordered tool list
- Extra env: `INCEPTION_AGENT_MAX_NUDGES=4`, `INCEPTION_FORCE_AUDIT_TOOL=1`

---

## Golden path (PASS)

**Script:** `scripts/run_architect_pipeline_golden.py`  
**Task ID:** `codebase-context-cache-v1`  
**Verify:** `verify/ARCHITECT_PIPELINE_GOLDEN.json`

Phases exercised: DESIGN (consult), REVIEW (chat), PLAN pre-seeded, VERIFY.json with real pytest stdout_tail.

### Example PLAN deliverable (YAML frontmatter required)

See `docs/PLAN.template.md` and pre-seeded plan in golden script.

---

## Evidence bundle CLI

```bash
python3 scripts/build_evidence_bundle.py \
  --task "roadmap-v4" \
  --files \
    docs/CODEBASE_MAP.md \
    modules/llm_router.py \
    modules/inception_adapter.py \
    modules/inception_agent_policy.py \
    modules/inception_agent_tools.py \
    modules/cursor_cli_adapter.py \
  --workspace /home/opr/oprai_lab \
  --out task_history/oprai_improve_lab/results/_ROADMAP_v4_evidence_bundle.md
```

Then reference bundle in prompt instead of 6 separate reads.

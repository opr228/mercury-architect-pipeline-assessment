# Gates, INCEPTION_MAX_GATE_TURNS, tool_choice

## Environment variables (agent path)

From `env/env.inception.mercury.example`:

| Variable | Example value | Role |
|----------|---------------|------|
| `LLM_PROVIDER` | `inception` | Mercury-only path |
| `INCEPTION_AGENT_MAX_STEPS` | `12` (example) / `40` (PLAN phase / v4 script) | Max tool loop iterations |
| `INCEPTION_MAX_GATE_TURNS` | `6`–`50` | Budget to block synthesis until evidence/deliverable |
| `INCEPTION_AGENT_MAX_NUDGES` | `2` (example) / `4` (v3 script) | Soft nudges when agent tries to finish early |
| `INCEPTION_FORCE_AUDIT_TOOL` | `1` | Step 0 `tool_choice=required` for audit/roadmap |
| `INCEPTION_AGENT_ROADMAP_SYNTHESIS_MAX_TOKENS` | `4096` | Long markdown write_file |
| `INCEPTION_AGENT_SYNTHESIS_MAX_TOKENS` | `2048` | Default synthesis |
| `INCEPTION_REALTIME` | `true` | Faster tool steps |
| `INCEPTION_DIFFUSING` | `true` | Diffusion decode hint |
| `OPRAI_MERCURY_ONLY` | `1` | Block cursor_cli in llm_router |

**Not used:** `OPRAI_MAX_GATE_TURNS`

## Phase budgets (TaskRunner sets env per phase)

| Phase | INCEPTION_AGENT_MAX_STEPS | INCEPTION_MAX_GATE_TURNS |
|-------|---------------------------|--------------------------|
| RECON | 16 | 20 |
| DESIGN | 12 | 15 |
| PLAN | 40 | 50 |
| IMPLEMENT | 32 | 50 |
| VERIFY | 8 | 10 |
| REVIEW | 12 | 15 |

v4 roadmap script overrides: steps=40, gates=50 (matches PLAN).

## tool_choice logic (`inception_adapter.py`)

```
if synthesis_step (last message role == "tool"):
    tool_choice = "none"          # allow text / write_file args generation
else:
    tools = tool_schemas()
    if forced_first AND steps == 0 AND INCEPTION_FORCE_AUDIT_TOOL:
        tool_choice = "required"  # step 0 only — audit/roadmap deliverables
    else:
        tool_choice = "auto"      # NEVER required on every turn (dLLM wrong-tool loops)
```

## Gate types

1. **mandatory_evidence_pending** — blocks synthesis until required reads done (`inception_agent_policy.py`)
2. **deliverable_write_pending** — blocks finish until `write_file` ok and file on disk
3. **Gate turns** — separate budget from nudges; when exhausted → fail-closed error

## dLLM read-loop signature (v4 failure)

From `logs/llm_trace_roadmap-v4-arch-36437f91.jsonl`:

- `tool_steps`: 0 → 39 (never reaches 40 write)
- `completion_tokens`: 0 on every tool step
- `synthesis_step`: false throughout
- `reasoning_tokens`: 50–708 per step
- `prompt_tokens`: grows ~3k → ~9k (accumulating thread)
- Final error: `agent exceeded max steps (40)`

## ToolCallLoopDetector (`inception_tool_helpers.py`)

- `identical_threshold`: 3
- `failing_threshold`: 4
- `high_tolerance_tools`: read_file, grep_search, list_directory, glob_search
- Re-read same path tolerated → contributes to read-loop on roadmap tasks

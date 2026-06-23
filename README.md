# Mercury Architect Pipeline — Assessment Artifacts

Public artifact bundle for reviewing a **diffusion-LLM (dLLM) agent harness**: tool/synthesis loop, phase gates, deliverable validation, and a documented read-loop failure case.

This repository contains **documentation and reference code snapshots only** — not a runnable product deployment.

## What's inside

| Path | Description |
|------|-------------|
| [`ARCHITECT_PIPELINE_ARTIFACTS/`](ARCHITECT_PIPELINE_ARTIFACTS/) | Full tree (59 files): adapter, policy, TaskRunner, validators, skills, tests, logs |
| [`ARCHITECT_PIPELINE_ARTIFACTS.tar.gz`](ARCHITECT_PIPELINE_ARTIFACTS.tar.gz) | Same content, compressed |

Start with:

1. [`ARCHITECT_PIPELINE_ARTIFACTS/INDEX.md`](ARCHITECT_PIPELINE_ARTIFACTS/INDEX.md) — manifest  
2. [`ARCHITECT_PIPELINE_ARTIFACTS/GAP_ANALYSIS.md`](ARCHITECT_PIPELINE_ARTIFACTS/GAP_ANALYSIS.md) — root cause + proposed fixes  
3. [`ARCHITECT_PIPELINE_ARTIFACTS/logs/TOOL_CALL_SUMMARY.md`](ARCHITECT_PIPELINE_ARTIFACTS/logs/TOOL_CALL_SUMMARY.md) — 40 reads, 0 writes  

## Problem statement

A roadmap generation task (`Phase=PLAN`) hit the step budget with **40 consecutive `read_file` calls** and **no `write_file`**. API traces show `completion_tokens=0` on every tool step — typical dLLM tool-only mode before synthesis.

## Architecture (high level)

```
User message + deliverable=
        ↓
Skills (dLLM rules + domain workflow)
        ↓
Agent loop: tool steps → evidence gates → synthesis → write_file
        ↓
Validators (plan / fabrication / verify schema)
        ↓
TaskRunner phase checkpoints
```

## License

MIT — assessment and reference material.

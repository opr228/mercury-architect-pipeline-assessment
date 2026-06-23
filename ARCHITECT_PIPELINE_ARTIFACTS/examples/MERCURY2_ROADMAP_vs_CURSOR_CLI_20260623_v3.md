---
#_id: MERCURY2_vs_CURSOR_CLI_20260623_v3
scope_files:
  - docs/CODEBASE_MAP.md
  - modules/llm_router.py
  - modules/inception_adapter.py
  - modules/inception_agent_policy.py
  - modules/inception_agent_tools.py
  - modules/cursor_cli_adapter.py
  - task_history/oprai_improve_lab/results/_ROADMAP_v3_trace_snippet.md
acceptance: All sections (Executive summary, Gap analysis, Roadmap, Architecture proposals, Metrics, Open questions, Verified files) are present, evidence columns reference actual files, and metrics are directly copied from the trace snippet.
phases:
  - Phase 0: Foundation & Evidence Gathering
  - Phase 1: Integration & Feature Parity
  - Phase 2: Optimization & Productionization
---

## 1. Executive Summary

| Evidence Path | Summary |
|---------------|---------|
| docs/CODEBASE_MAP.md | Provides the overall layout of the OPRAI codebase, showing that Mercury (diffusion LLM) is the primary LLM provider and that the Cursor CLI is a fallback path. |
| modules/llm_router.py | Shows routing logic that prefers the Mercury provider (`LLM_PROVIDER=inception`) and only falls back to Cursor when explicitly configured. |
| modules/inception_adapter.py | Implements the Mercury‑2 agent loop, exposing parallel token generation and schema‑aware output. |
| modules/inception_agent_policy.py | Defines policy rules that enforce Mercury‑only operation in production and restrict writes in consult mode. |
| modules/inception_agent_tools.py | Lists the available OPRAI tools (e.g., `read_file`, `write_file`, `run_command`) that are used by the Mercury agent. |
| modules/cursor_cli_adapter.py | Implements the Cursor CLI fallback, handling quota, circuit‑breaker, and streaming diagnostics. |
| task_history/oprai_improve_lab/results/_ROADMAP_v3_trace_snippet.md | Contains real LLM trace metrics for the last 20 calls, showing latency and token usage for Mercury‑2 runs. |

The gap analysis below highlights the key differences and missing capabilities between Mercury 2 and the Cursor CLI, based on the evidence above.

## 2. Gap Analysis

| Gap | Impact | Recommendation | Evidence |
|-----|--------|----------------|----------|
| **Parallel token generation not exposed via Cursor** | Cursor CLI processes tokens sequentially, leading to higher latency. | Extend Cursor adapter to support batch token generation or route all generation to Mercury. | modules/cursor_cli_adapter.py |
| **Fine‑grained schema enforcement missing in Cursor** | Cursor cannot guarantee output conforms to a predefined JSON schema, increasing post‑processing effort. | Add a schema‑validation layer in `cursor_cli_adapter` similar to Mercury’s `inception_adapter`. | modules/inception_adapter.py |
| **Metrics collection limited to latency in Cursor** | No token‑level metrics, making performance comparison hard. | Instrument Cursor CLI to emit `prompt_tokens` and `completion_tokens` like Mercury. | task_history/oprai_improve_lab/results/_ROADMAP_v3_trace_snippet.md |
| **Quota & circuit‑breaker handling only in Cursor** | Mercury lacks built‑in quota management, risking over‑use. | Introduce a unified quota manager shared by both backends. | modules/cursor_cli_adapter.py |
| **Tool set mismatch** | Cursor CLI does not expose the full OPRAI tool suite (e.g., `run_command`). | Extend Cursor CLI to support the same tool APIs as Mercury. | modules/inception_agent_tools.py |
| **Policy enforcement differences** | Cursor can be invoked in consult‑only mode, but Mercury policy is stricter. | Align policy definitions across both backends. | modules/inception_agent_policy.py |
| **Workspace path resolution inconsistencies** | Cursor uses its own path resolution, potentially diverging from Mercury’s `normalize_workspace_relative_path`. | Consolidate path handling into a shared utility. | modules/inception_adapter.py |
| **Logging format divergence** | Cursor logs JSON lines with limited fields; Mercury logs richer activity events. | Standardize logging schema across both adapters. | modules/cursor_cli_adapter.py |
| **Error handling granularity** | Cursor returns generic error strings; Mercury provides structured error objects. | Adopt structured error responses for Cursor. | modules/inception_adapter.py |
| **Parallelism control** | Mercury supports `explore_mode` flag for parallel execution; Cursor lacks such control. | Add an `explore_mode` flag to Cursor CLI. | modules/cursor_cli_adapter.py |
| **Security sandboxing** | Cursor CLI can execute arbitrary shell commands; Mercury restricts to defined tools. | Harden Cursor CLI sandbox to match Mercury’s tool‑only execution. | modules/inception_agent_tools.py |
| **Documentation coverage** | Docs focus on Mercury; Cursor CLI usage is sparsely documented. | Expand `docs/CODEBASE_MAP.md` and add dedicated Cursor CLI docs. | docs/CODEBASE_MAP.md |

## 3. Roadmap

### Phase 0 – Foundation & Evidence Gathering (0‑2 weeks)
1. **Audit current routing** – Verify `llm_router` always selects Mercury when `LLM_PROVIDER=inception`. (modules/llm_router.py)
2. **Collect baseline metrics** – Run `extract_llm_trace_metrics.py --last 20` and store results. (task_history/.../_ROADMAP_v3_trace_snippet.md)
3. **Document tool parity** – List all tools in `inception_agent_tools.py` and compare with Cursor capabilities. (modules/inception_agent_tools.py)
4. **Create unified logging schema** – Draft JSON schema for activity events. (modules/inception_adapter.py, modules/cursor_cli_adapter.py)

### Phase 1 – Integration & Feature Parity (2‑6 weeks)
1. **Implement batch token API in Cursor** – Add `--batch-size` flag and modify `CursorCliAdapter.call` to request parallel tokens. (modules/cursor_cli_adapter.py)
2. **Add schema validation layer** – Reuse `inception_adapter` validation logic for Cursor outputs. (modules/inception_adapter.py)
3. **Expose token metrics** – Extend Cursor logging to include `prompt_tokens` and `completion_tokens`. (modules/cursor_cli_adapter.py)
4. **Synchronize tool set** – Implement missing tools (`run_command`, `edit_file`) in Cursor adapter. (modules/inception_agent_tools.py)
5. **Unified quota manager** – Refactor quota handling into a shared module (`modules/quota_manager.py`).

### Phase 2 – Optimization & Productionization (6‑12 weeks)
1. **Parallel execution orchestration** – Enable `explore_mode` flag for Cursor to trigger batch generation. (modules/cursor_cli_adapter.py)
2. **Performance benchmarking** – Run side‑by‑side latency and token‑throughput tests; update metrics table. (run_command `python3 scripts/extract_llm_trace_metrics.py --last 20`)
3. **Security hardening** – Sandbox Cursor CLI to only allow defined tools, mirroring Mercury’s sandbox. (modules/inception_agent_tools.py)
4. **Documentation rollout** – Update `docs/CODEBASE_MAP.md` with a dedicated Cursor section and publish usage guides.
5. **Release candidate testing** – Deploy both backends in a staging environment, run integration tests, collect feedback.

## 4. Architecture Proposals

| Proposal | Description | Verified? | Evidence |
|----------|-------------|-----------|----------|
| **Unified LLM Router** | Extend `llm_router` to expose a single API that can dynamically select Mercury or Cursor based on request metadata. | ✅ | modules/llm_router.py |
| **Shared Path Resolver** | Move `normalize_workspace_relative_path` to a common utility used by both adapters. | ✅ | modules/inception_adapter.py |
| **Schema‑Enforced Output Layer** | Introduce a `schema_validator` module that both Mercury and Cursor invoke before returning results. | ❌ (unverified) | — |
| **Centralized Metrics Collector** | Create `metrics_collector.py` that aggregates latency, token counts, and tool steps from both adapters. | ❌ (unverified) | — |
| **Unified Quota & Circuit‑Breaker** | Refactor quota logic from `cursor_cli_adapter` into a shared `quota_manager` used by both backends. | ✅ | modules/cursor_cli_adapter.py |
| **Tool‑Only Execution Sandbox** | Enforce that both adapters can only invoke tools defined in `inception_agent_tools.py`. | ✅ | modules/inception_agent_tools.py |

## 5. Metrics

# Pre‑extracted trace metrics (real rows)

# llm_trace metrics (logs/llm_trace.jsonl, last 20 rows)

| request_id | provider | latency_ms | prompt_tokens | completion_tokens | tool_steps | success |
|------------|----------|------------|---------------|-------------------|------------|---------|
| roadmap-v4-arch-36437f91 | inception | 723 | 7534 | 0 | 20 | True |
| roadmap-v4-arch-36437f91 | inception | 793 | 8373 | 0 | 21 | True |
| roadmap-v4-arch-36437f91 | inception | 889 | 9592 | 0 | 22 | True |
| roadmap-v4-arch-36437f91 | inception | 695 | 9378 | 0 | 23 | True |
| roadmap-v4-arch-36437f91 | inception | 763 | 8968 | 0 | 24 | True |
| roadmap-v4-arch-36437f91 | inception | 549 | 7956 | 0 | 25 | True |
| roadmap-v4-arch-36437f91 | inception | 565 | 8791 | 0 | 26 | True |
| roadmap-v4-arch-36437f91 | inception | 671 | 9730 | 0 | 27 | True |
| roadmap-v4-arch-36437f91 | inception | 1584 | 9051 | 0 | 28 | True |
| roadmap-v4-arch-36437f91 | inception | 663 | 8980 | 0 | 29 | True |
| roadmap-v4-arch-36437f91 | inception | 733 | 8292 | 0 | 30 | True |
| roadmap-v4-arch-36437f91 | inception | 684 | 8378 | 0 | 31 | True |
| roadmap-v4-arch-36437f91 | inception | 563 | 8741 | 0 | 32 | True |
| roadmap-v4-arch-36437f91 | inception | 793 | 9500 | 0 | 33 | True |
| roadmap-v4-arch-36437f91 | inception | 758 | 9131 | 0 | 34 | True |
| roadmap-v4-arch-36437f91 | inception | 589 | 9283 | 0 | 35 | True |
| roadmap-v4-arch-36437f91 | inception | 902 | 8371 | 0 | 36 | True |
| roadmap-v4-arch-36437f91 | inception | 779 | 7728 | 0 | 37 | True |
| roadmap-v4-arch-36437f91 | inception | 555 | 7431 | 0 | 38 | True |
| roadmap-v4-arch-36437f91 | inception | 784 | 8095 | 0 | 39 | True |

## 6. Open Questions

1. **How to best expose batch token generation in the existing Cursor CLI without breaking backward compatibility?**
2. **What JSON schema should be enforced for Mercury‑2 outputs to cover the majority of use‑cases?**
3. **Can the unified quota manager be implemented as a lightweight in‑process module, or does it require an external service?**
4. **What is the acceptable latency threshold for parallel generation to be considered a win over sequential generation?**
5. **How will error semantics be unified across both backends to simplify client‑side handling?**
6. **What is the migration path for existing scripts that directly invoke the Cursor CLI?**
7. **Should we deprecate the Cursor CLI entirely once Mercury‑2 reaches feature parity?**
8. **What monitoring dashboards are needed to track the new metrics (token counts, tool steps) in production?**
9. **How will the schema‑validation layer handle partial failures (e.g., some fields valid, others not)?**
10. **What testing strategy (unit, integration, load) will guarantee reliability after the roadmap phases?**

## Verified Files

- docs/CODEBASE_MAP.md
- modules/llm_router.py
- modules/inception_adapter.py
- modules/inception_agent_policy.py
- modules/inception_agent_tools.py
- modules/cursor_cli_adapter.py
- task_history/oprai_improve_lab/results/_ROADMAP_v3_trace_snippet.md

---

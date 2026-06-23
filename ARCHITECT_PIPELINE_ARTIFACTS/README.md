# Mercury Architect Pipeline — artifact bundle

Evidence package for analyzing and fixing the OPRAI Mercury dLLM architect pipeline (roadmap v4 read-loop).

## Contents

- **INDEX.md** — manifest
- **GAP_ANALYSIS.md** — root cause + fix map
- **ARCHITECT_PIPELINE_ARTIFACTS.tar.gz** — same tree, compressed (sibling file in repo root when published)

## Quick start

```bash
tar -xzf ARCHITECT_PIPELINE_ARTIFACTS.tar.gz
cd ARCHITECT_PIPELINE_ARTIFACTS
cat GAP_ANALYSIS.md
cat logs/TOOL_CALL_SUMMARY.md
```

## Key finding

Roadmap v4: **40 `read_file` calls, 0 `write_file`** — dLLM stuck in re-read loop before synthesis.

## Reproduce (requires INCEPTION_API_KEY + OPRAI checkout)

```bash
python3 scripts/run_roadmap_v4_architect.py
```

## License

Same as parent OPRAI repository.

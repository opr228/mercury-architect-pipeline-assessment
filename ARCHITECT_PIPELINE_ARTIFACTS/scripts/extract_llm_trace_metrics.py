#!/usr/bin/env python3
"""Extract real metrics rows from llm_trace.jsonl for audit/roadmap deliverables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _resolve_trace_path(path: str, workspace: str | None) -> Path:
    p = Path(path)
    if p.is_file():
        return p
    if workspace:
        candidate = Path(workspace) / path
        if candidate.is_file():
            return candidate
    for base in (Path("/home/opr/oprai_lab"), Path("/home/opr")):
        candidate = base / path
        if candidate.is_file():
            return candidate
    return p


def _load_rows(trace_path: Path, last: int) -> List[Dict[str, Any]]:
    if not trace_path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-last:] if last > 0 else rows


def _format_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No rows found in llm_trace.jsonl"
    lines = [
        "| request_id | provider | latency_ms | prompt_tokens | completion_tokens | tool_steps | success |",
        "|------------|----------|------------|---------------|-------------------|------------|---------|",
    ]
    for r in rows:
        rid = str(r.get("request_id", "?"))[:48]
        lines.append(
            "| {request_id} | {provider} | {latency_ms} | {prompt_tokens} | {completion_tokens} | {tool_steps} | {success} |".format(
                request_id=rid,
                provider=r.get("provider", "not measured"),
                latency_ms=r.get("latency_ms", "not measured"),
                prompt_tokens=r.get("prompt_tokens", "not measured"),
                completion_tokens=r.get("completion_tokens", "not measured"),
                tool_steps=r.get("tool_steps", "not measured"),
                success=r.get("success", "not measured"),
            )
        )
    return "\n".join(lines)


def extract_metrics(
    path: str = "logs/llm_trace.jsonl",
    *,
    last: int = 30,
    workspace: str | None = None,
    fmt: str = "table",
) -> str:
    trace_path = _resolve_trace_path(path, workspace)
    rows = _load_rows(trace_path, last)
    if fmt == "json":
        return json.dumps({"path": str(trace_path), "rows": rows, "count": len(rows)}, indent=2)
    header = f"# llm_trace metrics ({trace_path}, last {len(rows)} rows)\n\n"
    return header + _format_table(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract real llm_trace metrics for Mercury deliverables")
    parser.add_argument("--path", default="logs/llm_trace.jsonl", help="Trace file path")
    parser.add_argument("--last", type=int, default=30, help="Number of recent rows")
    parser.add_argument("--workspace", default=None, help="Workspace root for relative path")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args()
    out = extract_metrics(args.path, last=args.last, workspace=args.workspace, fmt=args.format)
    print(out)
    return 0 if "No rows found" not in out else 1


if __name__ == "__main__":
    sys.exit(main())

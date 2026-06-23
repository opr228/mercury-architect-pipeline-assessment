#!/usr/bin/env python3
"""Pre-materialize evidence bundle for Mercury architect phases."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_bundle(files: list[str], workspace: str, out_path: str, task: str) -> str:
    ws = Path(workspace)
    lines = [f"# Evidence bundle: {task}", ""]
    for rel in files:
        p = ws / rel
        lines.append(f"## {rel}")
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > 8000:
                text = text[:8000] + "\n[...truncated]"
            lines.append("```")
            lines.append(text)
            lines.append("```")
        else:
            lines.append("(file not found)")
        lines.append("")
    body = "\n".join(lines)
    out = ws / out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return str(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--workspace", default="/home/opr/oprai_lab")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    path = build_bundle(args.files, args.workspace, args.out, args.task)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
# Smoke test Inception Mercury API (direct HTTP — not cursor agent).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/.."

if [[ -z "${INCEPTION_API_KEY:-}" ]]; then
  echo "ERROR: INCEPTION_API_KEY is not set (see oprai_lab/env.secrets.example)" >&2
  exit 1
fi

echo "=== Mercury 2 chat ==="
python3 - <<'PY'
import os, sys
sys.path.insert(0, "/home/opr")
from modules.inception_adapter import call_chat

r = call_chat(
    messages=[{"role": "user", "content": "What is 2+2? Reply with the number only."}],
    model=os.getenv("LLM_MODEL", "mercury-2"),
    max_tokens=64,
    temperature=0.5,
    reasoning_effort=os.getenv("INCEPTION_REASONING_EFFORT", "instant"),
)
print("success:", r.success)
print("latency_ms:", r.latency_ms)
print("content:", (r.content or "")[:200])
if not r.success:
    print("error:", r.error)
    sys.exit(1)
PY

echo ""
echo "=== Mercury Edit 2 FIM ==="
python3 - <<'PY'
import os, sys
sys.path.insert(0, "/home/opr")
from modules.inception_adapter import call_fim

r = call_fim(
    prompt="def add(a, b):\n    ",
    suffix="\n    return c",
    model=os.getenv("INCEPTION_EDIT_MODEL", "mercury-edit-2"),
    max_tokens=128,
)
print("success:", r.success)
print("latency_ms:", r.latency_ms)
print("text:", (r.content or "")[:200])
if not r.success:
    print("error:", r.error)
    sys.exit(1)
PY

echo ""
echo "OK — Inception Mercury smoke passed"

#!/usr/bin/env bash
# Smoke: Inception Mercury direct API + OPRAI agent tools.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/.."
for f in "$ROOT/../env.secrets" "$ROOT/env.secrets"; do
  [[ -f "$f" ]] && source "$f"
done
source "$ROOT/env.inception.mercury.example"

echo "=== Direct chat (instant) ==="
python3 - <<'PY'
import sys
sys.path.insert(0, "/home/opr")
from modules.inception_adapter import call_chat
r = call_chat(
    messages=[{"role": "user", "content": "Reply with exactly: ok"}],
    max_tokens=16,
    temperature=0.5,
    reasoning_effort="instant",
)
print("success:", r.success, "content:", (r.content or "")[:80], "latency_ms:", r.latency_ms)
if not r.success:
    sys.exit(1)
PY

echo ""
echo "=== Agent (tools catalog, no tool call expected) ==="
python3 - <<'PY'
import sys
sys.path.insert(0, "/home/opr")
from modules.inception_adapter import call_agent
r = call_agent(
    messages=[{"role": "user", "content": "List your available OPRAI tools in one short line."}],
    max_tokens=256,
    temperature=0.5,
    explore_mode=False,
    allow_writes=False,
)
print("success:", r.success)
print("tool_steps:", r.tool_steps)
print("content:", (r.content or "")[:300])
if not r.success:
    print("error:", r.error)
    sys.exit(1)
PY

if curl -sf http://127.0.0.1:5004/api/health >/dev/null 2>&1; then
  echo ""
  echo "=== /api/health/cursor (inception primary) ==="
  curl -s http://127.0.0.1:5004/api/health/cursor | python3 -m json.tool | head -25
fi

echo ""
echo "OK — Inception agent smoke passed"

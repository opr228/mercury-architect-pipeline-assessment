# Cursor CLI Runbook (OPRAI Production)

## Profile

Primary LLM path for root+ORK orchestrators:

- `LLM_PROVIDER=cursor_cli`
- `LLM_MODEL=auto`
- `CURSOR_CLI_COMMAND=cursor`
- `CURSOR_AGENT_WORKSPACE=/home/opr`
- `CURSOR_AGENT_MODE=ask` (read-only chat; no file writes)

## Authentication

### Option A — API key (headless / production)

1. Create key: [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations)
2. Copy example and fill secrets file:

```bash
cp /home/opr/env.cursor.secret.example /home/opr/env.cursor.secret
# edit CURSOR_API_KEY in env.cursor.secret
chmod 600 /home/opr/env.cursor.secret
source /home/opr/env.local
```

### Option B — Interactive login

```bash
cursor agent login
cursor agent status
cursor agent models
```

`env.local` auto-sources `/home/opr/env.cursor.secret` when present.

## Health checks

```bash
source /home/opr/env.local
curl http://127.0.0.1:5004/api/health/cursor
curl http://127.0.0.1:5004/api/auth/cursor-cli/status
curl http://127.0.0.1:5005/api/health/cursor
```

Expected when auth is configured:

- `primary_health.ok=true`
- `authenticated=true`

## Chat smoke

```bash
curl -X POST http://127.0.0.1:5004/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"ping","fast_mode":true}'
```

Fast mode returns adapter health without a full agent round-trip.

## Context layer (lean mode)

See [`CONTEXT_LAYER.md`](CONTEXT_LAYER.md). Default production profile:

- `OPRAI_CLI_PROFILE=lean` — no shell, read allowlist only
- Map/index injected into every full chat prompt
- `GET /api/context/status` — index age and prefix size

```bash
curl http://127.0.0.1:5004/api/context/status
```

Explore mode (`explore_mode: true` on chat) requires `OPRAI_EXPLORE_ALLOWED=1` after `tests/live/context_layer_regression.sh`.

Rate limit: `OPRAI_CHAT_RATE_LIMIT` full chats per client IP per hour (default 30).

## Project CLI config

Headless permissions: [`.cursor/cli.lean.json`](../.cursor/cli.lean.json) (default) and [`.cursor/cli.explore.json`](../.cursor/cli.explore.json). [`scripts/cursor-cli.sh`](../scripts/cursor-cli.sh) activates profile before `cursor agent`.

## Autonomy modes

| Autonomy mode | Cursor agent mode |
|---------------|-------------------|
| `propose` | `plan` or `ask` |
| `apply_once` / `apply_window` | full agent (requires arm + approval token) |

## Deprecated

Gemini CLI (`LLM_PROVIDER=gemini_web_subscription`) is legacy fallback only. Set `LLM_FALLBACK_PROVIDER=gemini_web_subscription` to re-enable on Cursor failure.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Not logged in` | Set `CURSOR_API_KEY` or run `cursor agent login` |
| `cli_not_found` | Ensure `cursor` is on PATH (remote-cli in Cursor installs) |
| `Authentication required` in chat | Restart API after sourcing `env.local` |
| Slow responses | Use `fast_mode=true` for health-only path; reduce prompt size |

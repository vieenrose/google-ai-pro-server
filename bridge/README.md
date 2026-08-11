# Gemini Bridge

Tiny zero-dependency HTTP service that lets external apps (like the Discourse
plugin) talk to Gemini through your **Google AI Pro subscription** — no Gemini
API key.

```
Discourse plugin (Ruby)  →  bridge (Python, localhost)  →  agy (Antigravity CLI)
                              POST /v1/chat                 Google AI Pro OAuth
                              POST /v1/deep-research         (keyring)
```

## Endpoints

| Endpoint | Body | Returns |
|---|---|---|
| `GET /health` | — | `{ok, backend, available, backend_info}` |
| `POST /v1/chat` | `{messages: [{role, content}], model?}` | `{reply, model, usage, error}` |
| `POST /v1/deep-research` | `{topic, max_questions?, model?}` | `{report, sources[], plan[], error}` ⚠ 1–5 min |

All endpoints require `Authorization: Bearer <BRIDGE_TOKEN>` unless you run
without a token.

## Run it

```bash
cd /path/to/google-ai-pro-server

# quick run (foreground)
BRIDGE_TOKEN=change-me python3 bridge/server.py --port 8787 --backend agy

# as a service (systemd) — recommended
sudo cp bridge/gemini-bridge.service /etc/systemd/system/gemini-bridge@$(whoami).service
sudo systemctl daemon-reload
sudo systemctl enable --now gemini-bridge@$(whoami)
```

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `BRIDGE_TOKEN` | (none) | Bearer token clients must send; empty = open |
| `BACKEND` | auto | `agy` (live search) · `direct` (knowledge only) · `gemini` · `mock` |
| `PORT` | `8787` | Listen port |
| `HOST` | `127.0.0.1` | Bind address — keep local unless you know what you're doing |

## Prerequisites on the host

```bash
# 1. Install + sign in once (interactive, with a browser)
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy
#    browser opens → sign in with your Google AI Pro account
#    copy the code from the redirect URL
#    paste it INTO the agy terminal → Enter

# 2. Sanity check the backend works headlessly
agy -p "Reply with exactly: ok" --output-format json

# 3. Start the bridge
BRIDGE_TOKEN=change-me python3 bridge/server.py --port 8787 --backend agy
```

## Test it

```bash
curl -s http://127.0.0.1:8787/health

curl -s -X POST http://127.0.0.1:8787/v1/chat \
  -H "Authorization: Bearer change-me" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"say hi"}]}'

curl -s -X POST http://127.0.0.1:8787/v1/deep-research \
  -H "Authorization: Bearer change-me" -H "Content-Type: application/json" \
  -d '{"topic":"AI news this week","max_questions":1}'   # ~1 min
```

## Notes for operators

- **Bind to `127.0.0.1`** and set `BRIDGE_TOKEN`; Discourse reaches it via the
  same host (or the host IP if Discourse is containerized — `127.0.0.1` inside
  a container is the container itself).
- The bridge is **stateless** — safe to restart; token refreshes happen
  automatically on the agy side (and via the refresh token in the OAuth file).
- Deep research is **slow and consumes AI Pro quota** — the Discourse plugin
  enforces per-user daily limits; keep them sane.
- `agy` auth can expire; re-run `agy` interactively to re-auth if `/health`
  shows the backend failing.

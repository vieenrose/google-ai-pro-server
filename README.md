# Sloth AI — Google AI Pro (Antigravity) bridge for Discourse

## What it does (and only that)

1. **AGY-OCI bridge** — connects Discourse to the Google AI Pro subscription
   via the Antigravity application backend. Native googleSearch grounding,
   image generation (auto-routed from any gemini-* model), and a deep-research
   command (`deep research: <topic>` on the chat model) — all in-process on
   the bridge, no SearXNG / smolagents / agent framework.
2. **Quota monitor** — public `/sloth-ai` + admin page: per-model remaining
   quota + reset countdowns from the Antigravity control plane.
3. **Subscription management** — Google AI Pro OAuth status + two-step PKCE
   re-auth in the admin page.

Everything else (model registry, API keys, agents, bots, summarization, …) is
handled by the **Discourse AI** plugin, which points its LLM models at this
bridge (`http://<host>:8787/v1/chat/completions`, open_ai provider).

## Layout

- `bridge/server.py`      — the bridge (Antigravity chat + quota + auth)
- `bridge/agy_auth.py`    — auth status + PKCE re-auth helpers
- `demo/gemini_backends.py` — Antigravity backend (native googleSearch)
- `demo/deep_research.py` — 3-phase deep-research workflow (plan/gather/write)
- `discourse-deep-research/` — the Discourse plugin (admin quota + auth page)

## Env (systemd)

- `BACKEND=antigravity-app`
- `BRIDGE_TOKEN`, `ANTIGRAVITY_TOKEN_FILE`, `ANTIGRAVITY_CLIENT_SECRET`
- `FORUM_BASE_URL`, `FORUM_API_KEY` (image upload), `FORUM_BOT_USERNAME`

## Add a model

In Discourse AI → AI LLMs, add an `open_ai` provider pointing at
`http://<bridge>:8787/v1/chat/completions` (e.g. gemini-3.7-flash-tiered,
claude-sonnet-4-6, claude-opus-4-6-thinking). Use the bridge token as the
API key.

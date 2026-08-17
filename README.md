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

## Final architecture

```
Discourse (forum posts + instant chat)
  └── Discourse AI
        ├── AI LLMs     → gemini-3.7-flash-tiered, claude-sonnet-4-6,
        │                 claude-opus-4-6-thinking  (all point at this bridge)
        ├── AI Agents   → 3 chat bots (mention → agent → LLM):
        │                 ai_gemini-3.7-flash, ai_claude-sonnet-4-6, ai_claude-opus-4-6
        ├── AI Secrets  → API keys (if any external provider)
        └── AI Bots     → all_bot_ids include each model's bot user
              │  POST /v1/chat/completions (open_ai provider)
              ▼
        Sloth AI bridge (Antigravity only — this repo)
              ├── native googleSearch grounding (Google subscription)
              ├── image generation: any gemini-* model auto-routes to the
              │   internal image model; no separate image bot needed
              ├── deep research: chat model + "deep research: <topic>" runs a
              │   3-phase plan/gather/write workflow (native grounding per call)
              ├── /sloth-ai       → public quota monitor
              └── /admin/plugins/sloth-ai → auth status + PKCE re-auth
```

### How users interact

| You type | What happens |
|---|---|
| `@ai_gemini-3.7-flash 你好` (topic or chat) | Discourse AI agent → bridge → Gemini 3.7 (native grounding) |
| `@ai_claude-sonnet-4-6 …` / `@ai_claude-opus-4-6 …` | Claude via bridge (Google AI Pro subscription) |
| `@ai_gemini-3.7-flash 畫一張企鵝` | bridge auto-routes to image model, uploads to forum |
| `@ai_gemini-3.7-flash deep research: 樹懶` | bridge runs the deep-research workflow, streams report |
| `GET /sloth-ai` | quota monitor (all users) |
| admin → Plugins → Sloth AI | subscription status + re-auth + quota |

### Notes

- The Sloth AI *plugin* provides only the bridge + quota + subscription
  management. Model registry, API keys, agents and reply routing are owned by
  Discourse AI. No SearXNG, no smolagents, no separate agents for
  image/deep-research (they run on the bridge).
- Deep research is a 3-phase workflow (plan → gather → synthesize) using the
  Antigravity backend, whose *per-call* web-search is native Google
  grounding. It is not the console-only Gemini Deep Research agent (that is
  not exposed via the subscription).

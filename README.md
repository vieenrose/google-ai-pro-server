# Google AI Pro Server

**Chat with Gemini and run Deep Research using only your Google AI Pro
subscription — no Gemini API key, no API billing.**

This repo contains a working Python demo (step 1 of a plan to build a
Discourse plugin that lets forum users summon Gemini into a topic or trigger
Deep Research).

## Status: ✅ live-verified (2026-08-11)

Tested end-to-end against a real Google account via the Antigravity backend:

```
$ python3 cli.py --backend direct "Say hello"
gemini> Hello from Gemini, it's great to meet you!

$ python3 cli.py --backend direct --max-questions 2 --deep "fusion energy…"
  ▸ Phase 1/3 — planning research on: …
  ▸ Phase 2/3 — researching question 1/2: …
  ▸ Phase 2/3 — researching question 2/2: …
  ▸ Phase 3/3 — synthesizing the final report…
✔ Report complete — 4 question(s), 13 source(s), 48s
# Comprehensive Research Report: … (with inline citations [1]…[13])
```

## How it works

| Layer | What it is |
|---|---|
| **Google AI Pro subscription** | Consumer subscription. Powers Gemini in the app, AI Studio, and the Antigravity CLI. **It does NOT grant Gemini API access** (billed separately). |
| **Antigravity CLI (`agy`)** | Google's official CLI (successor of Gemini CLI). Signs in with your Google account once (OAuth), then runs headless. |
| **Direct backend** | Talks to Google's internal Antigravity / Cloud Code Assist API (`cloudcode-pa.googleapis.com`) using the OAuth token `agy` caches. Technique recycled (MIT) from the community proxy. |
| **Deep Research workflow** | 3-phase research: plan → gather → synthesize, on the subscription backend. |

```
OpenAI-style client / REPL
        │
        ▼
┌──────────────────────────────────────────┐
│ demo/cli.py                              │
│  chat mode  → agy token → generateContent│
│  /deep mode → 3-phase research workflow  │
│  backends: direct · agy · gemini · mock  │
└──────────────────────────────────────────┘
```

## Quick start

```bash
# 1. Install Antigravity CLI and sign in once with your AI Pro Google account
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy

# 2. Run the demo (auto-selects the best backend)
cd demo
python3 cli.py "hello"                                  # one-shot chat
python3 cli.py --deep "quantum computing trends 2026"   # deep research
python3 cli.py                                          # interactive REPL
```

If `agy` isn't available on your machine, use the manual OAuth flow instead:

```bash
python3 demo/auth_agy.py        # prints a sign-in URL, saves the token
python3 demo/cli.py --backend direct "hello"
```

## Honest caveats ⚠️

1. **The subscription does not include the Gemini API.** This project uses the
   Antigravity backend instead — an unofficial integration that can break or be
   restricted at any time. The `agy` CLI itself is official, but automating it
   headlessly is outside its documented support envelope.
2. **Live search depends on the `agy` backend.** The full `agy` agent has
   authentic Google Search grounding (verified: `grounding-api-redirect` links
   + real current events). The `direct` backend (low-level endpoint) can only
   answer from training data.
3. **Deep Research is a workflow, not the paid agent.** The true Deep Research
   agent exists only in the Gemini app or as a *paid* API. Here it's a
   3-phase research workflow (plan → gather → synthesize) — with real search
   grounding when using the `agy` backend.
4. **Terms of service.** For personal/local use. Automating consumer
   subscriptions may violate Google's terms — don't resell or scale this.

## Repository layout

```
├── PLAN.md                 # full architecture plan (+ Discourse plugin roadmap)
└── demo/
    ├── cli.py              # chat + /deep research (REPL & one-shot)
    ├── gemini_backends.py  # direct (live) · agy · gemini · mock backends
    ├── deep_research.py    # 3-phase research workflow
    ├── auth_agy.py         # one-time manual OAuth sign-in (PKCE)
    └── README.md           # demo docs
```

## Roadmap

- [x] Live chat (sync + streaming) via the subscription
- [x] Deep Research workflow (plan → gather → synthesize) with citations
- [ ] HTTP bridge (`/v1/chat`, `/v1/deep-research`) for external tools
- [ ] Discourse plugin: summon Gemini into a topic, trigger Deep Research
- [ ] Optional: live web search (execute the `google_search` tool loop)
- [ ] Optional: pluggable paid-API backend for the *real* Deep Research agent

## License

MIT

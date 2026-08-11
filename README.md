# Google AI Pro Server

**Chat with Gemini and run Deep Research using only your Google AI Pro
subscription — no Gemini API key, no API billing.**

A complete stack: a **Python CLI demo**, a zero-dependency **HTTP bridge**, and
a **Discourse plugin** that lets forum users summon Gemini (`@gemini …`) or
trigger Deep Research (`/deep …`).

## Status: ✅ live-verified (2026-08-11)

Tested end-to-end against a real Google AI Pro account — chat and Deep Research
both work with **authentic Google Search grounding** (the same
`grounding-api-redirect` grounding the Gemini app uses):

```
$ python3 cli.py --backend agy "Say hello"
gemini> Hello! It is a pleasure to meet you…

$ python3 cli.py --backend agy --deep "major world events August 2026"
  ▸ Phase 1/3 — planning research on: …
  ▸ Phase 2/3 — researching question 1/1: …
  ▸ Phase 3/3 — synthesizing the final report…
✔ Report complete — 4 question(s), 26 source(s), 51s
# Global Political and Diplomatic Developments Report…
# (Mecca defense pact, US–Brazil visa dispute, Vietnam–Australia visit…)
```

## Architecture

```
Forum user  →  Discourse plugin (Ruby)  →  bridge (Python, localhost:8787)
                                              │
                    @gemini … → /v1/chat ─────┤
                    /deep …   → /v1/deep-research ─────┤
                                              │
                                              ▼
                              agy (Antigravity CLI, AI Pro OAuth)
                                              │
                              real Gemini — authentic Google Search grounding
```

| Layer | What it is |
|---|---|
| **Google AI Pro subscription** | Consumer subscription. Powers Gemini in the app and the Antigravity CLI. **It does NOT grant Gemini API access** (that's billed separately). |
| **Antigravity CLI (`agy`)** | Google's official CLI. Signs in once with your Google account (browser OAuth), then runs headless. The full agent has **live Google Search grounding**. |
| **Direct backend** *(optional)* | Talks to Google's internal Antigravity API using the OAuth token `agy` caches (community technique, recycled from an MIT proxy). Knowledge-only — no live search. |
| **Deep Research workflow** | 3-phase research: plan → gather → synthesize, with real search grounding when using the `agy` backend. |

## Quick start

```bash
# 1. Install Antigravity CLI and sign in once with your AI Pro Google account
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy
#    browser opens → sign in → copy code from redirect → paste INTO the agy terminal

# 2. Run the demo CLI (agy is auto-selected, live search enabled)
cd demo
python3 cli.py "hello"                                  # one-shot chat
python3 cli.py --deep "quantum computing trends 2026"   # deep research
python3 cli.py                                          # interactive REPL
```

If `agy` can't complete interactive auth on your machine, use the manual OAuth
flow instead:

```bash
python3 demo/auth_agy.py        # prints a sign-in URL, saves the token
python3 demo/cli.py --backend direct "hello"            # knowledge-only backend
```

## Discourse plugin (the main goal)

See **[`discourse-gemini/README.md`](discourse-gemini/README.md)** for the
complete install guide and **[`discourse-gemini/USAGE.md`](discourse-gemini/USAGE.md)**
for the end-user commands. In short:

```bash
# on the Discourse host:
curl -fsSL https://antigravity.google/cli/install.sh | bash && agy   # sign in once
BRIDGE_TOKEN=change-me python3 bridge/server.py --port 8787 --backend agy   # run bridge

cd /var/discourse/plugins
git clone https://github.com/vieenrose/google-ai-pro-server.git discourse-gemini
cd /var/discourse && ./launcher rebuild app
# → configure bridge URL/token + allowed groups in admin settings
```

Then users just post:

```
@gemini explain async/await        → Gemini replies in-thread
/deep fusion energy 2026           → Gemini posts a cited research report
```

## Available models (verified on the AI Pro direct backend)

Model names are the **alias keys** you put in the discourse-ai LLM record
(`name:` field); the bridge maps them to the Antigravity API. Verified
2026-08-11 with live calls.

| Alias (use in discourse-ai) | Works on direct | Tool calling | Notes |
|---|---|---|---|
| `gemini-3.6-flash` (+ `-low`, `-medium`, `-high`) | ✅ | ✅ | newest flash line |
| `gemini-3.5-flash` (+ `-low`) | ✅ | ✅ | default chat model |
| `gemini-3-flash` | ✅ | ✅ | |
| `gemini-3.1-pro` (+ `-low`, `-high`) | ✅ | ✅ | |
| `gemini-2.5-flash` | ✅ | ✅ | **recommended tool-capable model** |
| `gemini-2.5-pro` | ✅* | ✅ | \* frequent `503 MODEL_CAPACITY_EXHAUSTED` |
| `claude-sonnet-4-6` | ✅ | ✅ | |
| `claude-opus-4-6-thinking` | ✅ | ✅ | |
| `gpt-oss-120b` | ❌ 404 | – | not served on direct |
| `gemini-3.5-flash-lite` | ❌ 404 | – | exists in agy picker, not on direct API |
| `gemini-3.1-flash-lite-preview` | ❌ 404 | – | |

Notes:
- "Tool calling" works on all listed models through the bridge (client headers
  + tool-friendly system prompt are applied automatically).
- `gemini-2.5-pro` is the full-size tool-capable model but is frequently
  capacity-exhausted (503); `gemini-2.5-flash` is the reliable default for
  tool-based agents.
- The `agy` interactive picker may show more names (e.g. lite variants) that
  are not servable through the `direct` backend.

## Choosing the right mode ⚖️

Three ways to get AI answers in the forum — they use **different knowledge
bases** and are complementary:

| | **Forum Researcher**<br/>(Discourse AI) | **Deep Research**<br/>(`@deep-research`) | **Plain summon**<br/>(`@Forum_Helper_bot`, `@ai_<model>`) |
|---|---|---|---|
| **Knows** | Your forum's posts only | The web / model knowledge | The current thread + model |
| **Searches** | Posts via `PostsFilter` (keywords, topics, users, categories) | Live Google Search in the official app; knowledge-only on the `direct` backend | nothing (uses thread history as context) |
| **Workflow** | understand → plan filter → dry-run count → refine → batch-analyze → summarize | plan → gather per question → synthesize report | single LLM call with thread memory |
| **Output** | insights + **citations to forum posts** `[ref](/t/-/topic/post)` | structured report + **URL sources** | conversational answer |
| **Cost** | per-post analysis (batched, dry-run first) | report workflow | one call |
| **Best for** | "what have we discussed about X?" with receipts | "what does the world say about X?" with sources | "answer my question in the context of this thread" |

The Poe-style model picker (`@ai_<model>`) gives you the *plain summon* mode
with a choice of backend models — every reply is tagged with the driving model
(`— ⚙️ 由 <model> 驅動`).

## Feature matrix by model ⚡

Capabilities depend heavily on *which model* and *which backend* you use.
Verified 2026-08-11 against the live AI Pro direct API and the OpenAI-compatible
bridge endpoint.

| Capability | gemini-3.5-flash<br/>(direct) | gemini-2.5-flash<br/>(direct) | gemini-2.5-pro<br/>(direct) | agy<br/>backend |
|---|---|---|---|---|
| Streaming chat (OpenAI-compat) | ✅ | ✅ | ✅ | ✅ |
| Multi-turn conversation memory | ✅ | ✅ | ✅ | ✅ |
| **Tool calling** (functionDeclarations) | ✅ | ✅ | ✅ (frequent 503) | ✅ |
| **Forum research** (search tool + citations) | ✅ | ✅ | ✅ | ✅ |
| **Web search tool** (discourse-ai / Google CSE) | ✅ \* | ✅ \* | ✅ \* | ✅ \* |
| Live Google Search **grounding** (in-model) | ❌ | ❌ | ❌ | ✅ |
| Topic summaries / AI Helper | ✅ | ✅ | ✅ | ✅ |
| Deep Research (3-phase workflow) | ✅ knowledge | ✅ | ✅ | ✅ live search |

\* needs a web-search provider key (Google CSE) configured in discourse-ai;
the *tool calling* works, the search backend is a separate credential.

**How to pick a model for discourse-ai agents**

- Chat / Helper / Summarizers → `gemini-3.5-flash` (fast, no tools needed).
- Tool-based agents (Forum Researcher, Web Researcher) → `gemini-2.5-flash`
  or `gemini-2.5-pro` — register a second LLM record pointing at the same
  bridge URL with `name: gemini-2.5-flash`.
- Tool calling works on **every** model when the request carries the Antigravity
  client headers (`User-Agent` / `X-Goog-Api-Client` / `Client-Metadata`) and a
  tool-friendly system prompt — the bridge sends both automatically.
  (Earlier reports of 404 on non-2.5 models were a missing-header artifact of
  the test harness.) `gemini-2.5-pro` is frequently capacity-exhausted
  (HTTP 503 `MODEL_CAPACITY_EXHAUSTED`), so `gemini-2.5-flash` is the reliable
  tool-capable default.
- The `agy` backend is the *only* path to authentic Google Search grounding
  (official Gemini-app-style answers), but it requires interactive/keyring
  auth — not headless-friendly on a server.

## Honest caveats ⚠️

1. **The subscription does not include the Gemini API.** This project uses the
   Antigravity backend instead — an unofficial integration that can break or be
   restricted at any time. The `agy` CLI itself is official, but automating it
   headlessly is outside its documented support envelope.
2. **Live search depends on the `agy` backend.** The full `agy` agent has
   authentic Google Search grounding (verified). The `direct` backend can only
   answer from training data.
3. **Deep Research is a workflow, not the paid agent.** The true Deep Research
   agent exists only in the Gemini app or as a *paid* API. Here it's a 3-phase
   research workflow (plan → gather → synthesize) — with real search grounding
   when using the `agy` backend.
4. **Terms of service.** For personal/local use. Automating consumer
   subscriptions may violate Google's terms — don't resell or scale this.

## Repository layout

```
├── PLAN.md                    # architecture plan
├── demo/                      # CLI demo (chat + /deep)
│   ├── cli.py                 # REPL + one-shot
│   ├── gemini_backends.py     # agy (live) · direct · gemini · mock
│   ├── deep_research.py       # 3-phase research workflow
│   ├── auth_agy.py            # manual PKCE OAuth helper
│   └── README.md
├── bridge/                    # HTTP bridge for external apps
│   ├── server.py              # /health, /v1/chat, /v1/chat/completions (OpenAI), /v1/deep-research
│   ├── gemini-bridge.service  # systemd unit example
│   └── README.md
└── discourse-gemini/          # Discourse plugin
    ├── plugin.rb              # triggers, bot user, permissions, limits
    ├── lib/gemini_bridge.rb   # Ruby HTTP client
    ├── jobs/regular/          # chat / deep-research / notice jobs
    ├── config/                # settings + locales
    ├── README.md              # install guide
    └── USAGE.md               # end-user commands
```

## Roadmap

- [x] Live chat (sync + streaming) via the subscription
- [x] Deep Research workflow with citations
- [x] Authentic Google Search via the `agy` full agent
- [x] HTTP bridge (`/v1/chat`, `/v1/deep-research`)
- [x] Discourse plugin: `@gemini` summon + `/deep` research
- [ ] Streamed bot replies (progress while researching)
- [ ] Multi-turn `@gemini` conversation chaining
- [ ] Optional: pluggable paid-API backend for the *real* Deep Research agent

## License

MIT

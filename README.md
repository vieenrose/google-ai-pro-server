# Google AI Pro Server

**Deep Research for Discourse, and the story of running this forum's AI on a
Google AI Pro subscription.**

The stack: a zero-dependency **HTTP bridge** (multi-backend: official Gemini
API / Antigravity CLI / cloudcode), a **Discourse plugin** for Deep Research
(`@deep-research …` / `/deep …`), and the tooling to wire **Discourse AI's
native Gemini support** to the same subscription.

> **2026-08-13 — Native-first:** the forum's `@ai_*` chat agents now run on
> Discourse AI's **built-in Gemini provider** (official Gemini API with an AI
> Studio key from the same Google account). The bridge remains for Deep
> Research and the cloudcode-only models (Claude / image gen). See
> [Native Discourse AI migration](#native-discourse-ai-migration-2026-08-13)
> for the why and the how.

## Status: ✅ live-verified (2026-08-11, updated 2026-08-13)

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

## Native Discourse AI migration (2026-08-13)

### Why

The bridge originally consumed the subscription through the **cloudcode
(Antigravity) side-channel** — the OAuth token from the Antigravity CLI.
On 2026-08-13 that path started returning `429 RESOURCE_EXHAUSTED` for every
model. The diagnosis:

- the account's per-model quota buckets were **all ~99% full** — nothing was
exhausted client-side
- `loadCodeAssist` showed `currentTier: free-tier` while `paidTier:
g1-pro-tier` — Google serves this account under the **Antigravity free-tier
request cap**, even though the same Google account holds an active Google AI
Pro (5 TB) subscription (confirmed on one.google.com)
- the AI Pro → Code Assist/Antigravity entitlement sync had not flipped the
active tier, and there is no API to force it

Meanwhile the **official Gemini API** (aistudio.google.com) on the same
account was explored with three keys (2026-08-13):

- **free-tier key** — flash models work (`gemini-3.6-flash`,
  `gemini-3.5-flash`, `gemini-3.1-flash-lite`); pro / image / googleSearch
  grounding models return `429 … free_tier_requests, limit: 0` (no free tier)
- **prepaid Tier-1 key** — all models enabled, but requires **prepayment**;
  the API is billed separately from the AI Pro subscription
- decision: **flash-only on the free-tier key** (zero extra cost); pro models
  deliberately skipped; a prepaid key can be dropped in later without code
  changes (just replace `api_key` on the LLM records)

Conclusion: use Discourse AI's built-in Gemini provider for the chat agents
(flash), and keep the bridge for what the native path cannot do (the Deep
Research pipeline and cloudcode-only Claude models).

### How

Discourse AI's Gemini endpoint is selected with `LlmModel.provider = "google"`
(source: `completions/endpoints/gemini.rb`, `can_contact?`). Per LLM record:

| Field | Value |
|---|---|
| `provider` | `google` |
| `name` | official API model id (e.g. `gemini-3.6-flash`) |
| `url` | `https://generativelanguage.googleapis.com/v1beta/models/<id>` |
| `api_key` | AI Studio key created on the **same Google account** as the AI Pro subscription |
| `vision_enabled` | `true` |

Migrated in place (DB-only, no rebuild, forum stays up):

| Forum LLM slug | Native API model | Verified |
|---|---|---|
| `gemini-3.6-flash` | `gemini-3.6-flash` | ✅ text, streaming, functionCall emission |
| `gemini-3.5-flash` | `gemini-3.5-flash` | ✅ text |
| `gemini-2.5-flash` | `gemini-3.1-flash-lite` | ✅ text (2.5-flash retired upstream) |
| `gemini-3.1-pro` | `gemini-3.1-pro-preview` | ⚠️ quota (free-tier limit 0 on this key — same tier sync pending) |
| `gemini-2.5-pro` | `gemini-3.1-pro-preview` | ⚠️ quota (2.5-pro retired upstream) |
| `claude-sonnet-4-6` / `claude-opus-4-6` | — (cloudcode-only) | stays on the bridge; replies a clear error until the Antigravity tier syncs |
| `gemini-3.1-flash-image` / Nano Banana 2 | — (image quota 0 on this key) | mechanism verified (see feature matrix) |

**Remaining cloudcode/tier blocker:** the AI Pro subscription is visible to
the cloudcode service (`paidTier: g1-pro-tier`) but the active tier is still
`free-tier` (Google-side entitlement sync). Once it flips — or Google lifts
the per-model `free_tier` limits on this key — pro models, Claude and image
generation come back automatically; no forum changes needed.

## Architecture

```
Forum user
  │
  ├─ @ai_* chat agents ──→ Discourse AI built-in Gemini endpoint
  │                          (generativelanguage.googleapis.com, API key)
  │
  └─ @deep-research / /deep ──→ discourse-deep-research plugin (Ruby)
                                 → bridge (Python, localhost:8787)
                                    → /v1/deep-research (3-phase pipeline)
                                       → gemini-api backend (flash models)
```

| Layer | What it is |
|---|---|
| **Google AI Pro subscription** | Consumer subscription (5 TB plan). Powers the Gemini app. **It does NOT grant Gemini API paid-tier access** — the API is billed separately (prepayment for Tier 1); the free tier is used here at zero cost. |
| **Native Discourse AI** | The forum's `@ai_*` agents run on Discourse AI's built-in Gemini provider (`LlmModel.provider = "google"`) with an AI Studio key from the same Google account. |
| **discourse-deep-research plugin** | Handles `@deep-research …` / `/deep …`; calls the bridge's multi-phase research pipeline. |
| **bridge (gemini-api backend)** | OpenAI-compatible HTTP bridge with multiple backends; `gemini-api` talks to the official Gemini API (flash models). Also has `direct` (cloudcode) and `agy` backends for the Antigravity subscription path. |
| **Deep Research workflow** | 3-phase research: plan → gather → synthesize. Knowledge-based on the API key (googleSearch grounding requires Tier 1, verified 429 on the free tier). |

## Quick start

**Native forum agents (recommended)** — configure the discourse-ai LLM records
with `provider: google` + your AI Studio key (see
[Native Discourse AI migration](#native-discourse-ai-migration-2026-08-13));
no bridge needed for chat.

**Deep Research** — run the bridge with the official API backend and install
`discourse-deep-research`:

```bash
BRIDGE_TOKEN=change-me GEMINI_API_KEY=<your-key> \
  python3 bridge/server.py --port 8787 --backend gemini-api
```

**Demo CLI** (chat / deep research on the host):

```bash
cd demo
python3 cli.py --backend gemini-api "hello"      # GEMINI_API_KEY env required
python3 cli.py --backend gemini-api --deep "quantum computing trends 2026"
```

**Antigravity subscription path** (cloudcode / agy, live-search-capable when
the tier syncs):

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy   # browser OAuth sign-in (interactive)
python3 demo/auth_agy.py   # or: manual OAuth flow — prints URL, saves token
python3 demo/cli.py --backend direct "hello"
```

## Discourse plugin — Google Deep Research

See **[`discourse-deep-research/README.md`](discourse-deep-research/README.md)**
for the complete install guide and
**[`discourse-deep-research/USAGE.md`](discourse-deep-research/USAGE.md)** for
the end-user commands. In short:

```bash
# on the Discourse host:
BRIDGE_TOKEN=change-me GEMINI_API_KEY=… python3 bridge/server.py --port 8787 --backend gemini-api

cd /var/discourse/plugins
cp -r /opt/google-ai-pro-server/discourse-deep-research discourse-deep-research
cd /var/discourse && ./launcher rebuild app
# → configure bridge URL/token + allowed groups in admin settings
```

Then users just post:

```
@deep-research fusion energy 2026   → bot posts a structured, sourced research report
/deep quantum computing trends      → same, slash-command form
```

(The former `@gemini` chat feature was removed in v0.2.0 — chat is handled by
the native `@ai_*` agents.)

## Available models (native Gemini API, verified 2026-08-13)

Model names are the **API model ids** in the discourse-ai LLM record
(`name:` field). Verified with live calls on the free-tier key.

| Model id (LLM `name:`) | Native API status | Tool calling | Notes |
|---|---|---|---|
| `gemini-3.6-flash` | ✅ text, streaming, functionCall | ✅ | forum default agent |
| `gemini-3.5-flash` | ✅ | ✅ | default chat / summarizer model |
| `gemini-3.1-flash-lite` | ✅ | ✅ | replaces retired `gemini-2.5-flash` |
| `gemini-3-flash-preview` | ✅ | ✅ | |
| `gemini-3.1-pro-preview` | ❌ 429 free-tier limit 0 | – | needs prepaid Tier 1 key |
| `gemini-2.5-pro` | ❌ retired upstream | – | alias → `gemini-3.1-pro-preview` |
| `claude-sonnet-4-6` / `claude-opus-4-6` | ❌ not on Gemini API | – | cloudcode-only; bridge replies a clear error |
| `nano-banana-pro-preview` (Nano Banana 2) | ❌ 429 free-tier limit 0 | – | image gen; needs Tier 1 |
| `gemini-3.1-flash-image` | ❌ 429 free-tier limit 0 | – | image gen; needs Tier 1 |
| googleSearch grounding (`tools: [{googleSearch: {}}]`) | ❌ 429 free-tier limit 0 | – | live web search; needs Tier 1 |
| `gemini-3.5-flash` (+ `-low`) | ✅ | ✅ | default chat model |
| `gemini-3-flash` | ✅ | ✅ | |
| `gemini-3.1-pro` (+ `-low`, `-high`) | ✅ | ✅ | |
| `gemini-2.5-flash` | ✅ | ✅ | **recommended tool-capable model** |
| `gemini-2.5-pro` | ✅* | ✅ | \* frequent `503 MODEL_CAPACITY_EXHAUSTED` |
| `claude-sonnet-4-6` | ✅ | ✅ | |
## Choosing the right mode ⚖️

Three ways to get AI answers in the forum — they use **different knowledge
bases** and are complementary:

| | **Forum Researcher**<br/>(Discourse AI) | **Deep Research**<br/>(`@deep-research`) | **Plain summon**<br/>(`@Forum_Helper_bot`, `@ai_<model>`) |
|---|---|---|---|
| **Knows** | Your forum's posts only | The web / model knowledge | The current thread + model |
| **Searches** | Posts via `PostsFilter` (keywords, topics, users, categories) | knowledge-based 3-phase pipeline (live search grounding needs Tier 1) | nothing (uses thread history as context) |
| **Workflow** | understand → plan filter → dry-run count → refine → batch-analyze → summarize | plan → gather per question → synthesize report | single LLM call with thread memory |
| **Output** | insights + **citations to forum posts** `[ref](/t/-/topic/post)` | structured report + **URL sources** | conversational answer |
| **Cost** | per-post analysis (batched, dry-run first) | report workflow | one call |
| **Best for** | "what have we discussed about X?" with receipts | "what does the world say about X?" with sources | "answer my question in the context of this thread" |

The Poe-style model picker (`@ai_<model>`) gives you the *plain summon* mode
with a choice of backend models — every reply is tagged with the driving model
(`— ⚙️ 由 <model> 驅動`).

## Feature matrix by model ⚡

Verified live 2026-08-13 against the **official Gemini API** (native Discourse
AI provider + bridge `gemini-api` backend, free-tier key). Rows marked ✅ were
tested end-to-end with real calls.

| Capability | gemini-3.6-flash | gemini-3.5-flash | gemini-3.1-flash-lite | pro / image models | Notes |
|---|---|---|---|---|---|
| Text chat | ✅ | ✅ | ✅ | ❌ quota | pro/image = free-tier limit 0 |
| Streaming (SSE, OpenAI-compat) | ✅ | ✅ | ✅ | ❌ | bridge + native both verified |
| Multi-turn memory | ✅ | ✅ | ✅ | ❌ | discourse thread context |
| **Tool calling** (functionDeclarations) | ✅ | ✅ | ✅ | ❌ | flash auto-emits functionCalls |
| **Forum research** (search tool + citations) | ✅ | ✅ | ✅ | ❌ | via Discourse AI tools |
| **Image input** (upload → model sees it) | ✅ | ✅ | ✅ | ❌ | inlineData verified |
| **Image generation via function-call** | ✅* | ✅* | ✅* | ❌ | flash emits `generate_image` calls with optimized prompts; image models blocked by quota |
| **Image generation** (direct output) | ❌ | ❌ | ❌ | ❌ | flash returns SVG/text only; image models = 429 on free tier |
| **Live web search grounding** (`googleSearch`) | ❌ | ❌ | ❌ | ❌ | 429 on free-tier key (needs Tier 1 prepay) |
| **Deep Research pipeline** (3-phase, plugin) | ✅ | ✅ | ✅ | ❌ | knowledge-based (no live search on free tier) |
| Thinking (details blocks in forum) | ✅ | ✅ | ✅ | ❌ | verified live: `<details class='ai-thinking'>` |
| Topic summaries / AI Helper | ✅ | ✅ | ✅ | ❌ | |

Key findings (2026-08-13, free-tier key `AQ.Ab8RN6I2Xv…`):

- **Flash models are the free tier** — `gemini-3.6-flash`, `gemini-3.5-flash`,
  `gemini-3.1-flash-lite`, `gemini-3-flash-preview` all work with zero cost.
- **Everything else needs the prepaid Tier 1**: pro models, both image models
  (`nano-banana-pro-preview` / Nano Banana 2, `gemini-3.1-flash-image`) and
  `googleSearch` grounding all return
  `429 … generate_content_free_tier_requests, limit: 0` on the free-tier key.
- **Multimodal-out mechanism verified**: give 3.6-flash a `generate_image`
  function declaration and it auto-emits a `functionCall` with an *improved*
  image prompt — the bridge can then call the image model and return the image
  as a `functionResponse`. Only the image models' quota blocks the last step.
- **`gemini-2.5-flash`/`gemini-2.5-pro` are retired upstream** ("no longer
  available to new users"); the forum aliases them to
  `gemini-3.1-flash-lite` / `gemini-3.1-pro-preview`.
- **Claude models** exist only on the cloudcode (Antigravity) side; the bridge
  replies with a clear note instead of failing silently.

**How to pick a model for discourse-ai agents (current state)**

- Default chat agent → `gemini-3.6-flash` (frontier flash, tools, thinking).
- Summarizer / helper → `gemini-3.5-flash`.
- Legacy 2.5-flash agents → aliased to `gemini-3.1-flash-lite`.
- Pro / image / Claude → configure the records now; they answer with a clear
  error until the account gets Tier 1 (prepay) or the cloudcode tier syncs.

## Antigravity SDK / MCP migration status 🔬

Explored 2026-08-12: the official `google-antigravity` Python SDK
(pip, v0.1.10) exposes `Agent`, `LocalAgentConfig`, multimodal types
(`Image`/`Audio`/`Video`), `ThinkingLevel`, tools and MCP-server
connections — the intended replacement for the reverse-engineered
`direct` backend.

**Auth finding:** the SDK's local connection requires a
`GEMINI_API_KEY` (env or `LocalAgentConfig(api_key=...)`) — it does
**not** use the Google AI Pro subscription OAuth by default. So:

| Path | Auth | Status |
|---|---|---|
| SDK + Gemini API key | API key (free tier exists) | ✅ official, reliable; separate billing |
| SDK + AI Pro OAuth | agy CLI keyring auth | ⛔ blocked headless (keyring + 60s interactive window) |
| MCP servers | follow the SDK/CLI auth | tools via `mcp_config.json` once auth is solved |

The bridge currently runs the fully-verified `direct` backend; migrating
to the SDK (API-key path) is the recommended next step for official
stability, keeping the AI Pro bridge as fallback.

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

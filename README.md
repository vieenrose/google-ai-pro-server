# Google AI Pro Server

**Deep Research for Discourse, and the story of running this forum's AI on a
Google AI Pro subscription — no Gemini API key, no API prepayment.**

The stack: a zero-dependency **HTTP bridge** (multi-backend: Antigravity app /
cloudcode / Gemini API / agy), and a **Discourse plugin** for Deep Research
(`@deep-research …` / `/deep …`). Discourse AI's `@ai_*` agents point at the
bridge, which routes everything through the **Antigravity application
backend** so the forum consumes the Google AI Pro subscription quota.

> **2026-08-13 (final) — Subscription-backed:** the forum's `@ai_*` agents,
> Gemini Google Search grounding, and `@deep-research` all run on the
> **Antigravity application backend** (`daily-cloudcode-pa.googleapis.com`),
> authenticated by the Google AI Pro OAuth account. **No Gemini API key and no
> API prepayment are used.** See
> [Antigravity application backend](#antigravity-application-backend-2026-08-13).

## Status: ✅ live-verified (2026-08-11, updated 2026-08-13)

End-to-end on a real Google AI Pro account:

| Capability | Result |
|---|---|
| Gemini chat via `@ai_*` agents | ✅ subscription quota |
| Google Search grounding (web-app style) | ✅ `googleSearch` + citations |
| Gemini 3.1 **Pro** / Claude Sonnet & Opus | ✅ (works on the app backend) |
| `@deep-research` multi-phase report | ✅ 12.5k-char report, 39 web sources |
| Image generation (topic + instant chat) | ✅ inline rendering (relative URLs) |
| Model thinking | ✅ folded in `<details class='ai-thinking'>` (Claude + Gemini) |
| Multi-host access (LAN + Tailscale) | ✅ split-horizon DNS, hostname-based URLs |
| Model footer tag (`— ⚙️ 由 X 驅動`) | 🗑️ removed — redundant with the `@ai_*` alias |

## Multi-host access (LAN + Tailscale) — 2026-08-13

The forum is reachable as `192.168.40.37` (LAN) and `raspberrypi` /
`100.112.145.8` (Tailscale MagicDNS). A single canonical hostname cannot be
two addresses, so the forum now references **one name — `raspberrypi`** — and
each network resolves it its own way (split-horizon DNS):

| Network | Resolution of `raspberrypi` |
|---|---|
| Tailscale | MagicDNS → `100.112.145.8` (built-in, stable) |
| LAN | `dnsmasq` on the Pi (`address=/raspberrypi/192.168.40.37`) — LAN clients use the Pi as DNS (set on the router DHCP) |

Discourse side:

- `SiteSetting.force_hostname = "raspberrypi"` (runtime, overrides the env)
- `DISCOURSE_HOSTNAME: "raspberrypi"` in `app.yml` for future rebuilds
- posts rebaked — asset URLs (emoji, avatars, oneboxes) now bake
  **relative** or `//raspberrypi/…`, so they render on both networks

**Why hostnames, not IPs:** the LAN IP is DHCP-assigned and can change.
Because Discourse/posts/bridge now reference the *name*, an IP change only
needs one line updated in `/etc/dnsmasq.d/raspberrypi.conf` — no rebuild, no
rebake. Pinning the IP via a router DHCP reservation removes even that.

The bridge uploads generated images with **relative URLs**
(`/uploads/...`) for the same reason (see `upload_to_forum`).

## AI reply rules (forum behavior) — 2026-08-14

Implemented in the discourse-ai patch (upstream PR #42536) + the
discourse-deep-research plugin, and validated live in the testing area.

| Rule | Behavior |
|---|---|
| **R1 — nesting** | The AI answer is posted at the same depth as the request: a request in a new post → answer in a new post; a request in a reply to post X → answer as a reply to X (sibling of the request). |
| **R2 — single responder** | In any post, the FIRST explicitly mentioned AI (in typing order) handles the request. Only one responder, ever. |
| **R3 — no proactive replies** | Replying to an AI's post without mentioning anyone does NOT trigger that AI (public topics). Mentions are the only trigger. |
| **R4 — AI posts may summon (one hop)** | If an AI's own post explicitly mentions an agent (e.g. a model writing "I'll ask @ai_x to regenerate"), that agent is triggered — but only when the AI post directly follows a human post. Bot→bot chains are impossible. |
| **Q — quotes don't count** | Mentions inside `[quote]` blocks are ignored — only what the user actually typed can trigger. |
| **PMs** | Keep the stock dynamic: a bot invited to a private message may answer messages that do not mention it. |

Trigger matrix (public topics):

| Post type | No mention | AI mention |
|---|---|---|
| New post | nothing | first mentioned answers |
| Reply to human | nothing | first mentioned answers |
| Reply to AI | nothing (R3) | first mentioned answers (R2) |
| AI post after a human post | nothing | mentioned agent answers (R4, one hop) |

Queue hardening: per-post dedup lock (`ai_reply_dedup:<post_id>`), deep
research runs on the `low` Sidekiq queue so it cannot block instant replies.

## Antigravity application backend (2026-08-13)

### Why this is the answer

Three backend routes were evaluated on the same Google account:

| Route | Quota | Search | Status |
|---|---|---|---|
| Gemini API (AI Studio key) | free tier (flash only); paid tier needs **prepayment** | grounding = paid | ❌ not the subscription |
| cloudcode-pa.googleapis.com | `paidTier: g1-pro-tier` visible but served as `free-tier` → 429 | – | ❌ Google-side sync bug |
| **Antigravity app** (`daily-cloudcode-pa.googleapis.com`) | **Google One / AI Pro subscription** | **`googleSearch` native tool** | ✅ works |

`paidTier` is not a client-selectable flag. The working path is Antigravity's
**application request envelope**: the daily Cloud Code control plane plus an
agent envelope carrying the model enum, session/trajectory IDs, and labels.
Google's backend then selects the subscription tier itself.

### Verified

- `gemini-3.6-flash-low` → 200
- `gemini-3.1-pro-low` → 200 (**Pro model on subscription quota**)
- `googleSearch` grounding → 200 with `groundingMetadata` + `groundingChunks`
- `gemini-3.1-flash-image` → 200 with `inlineData`
- `@deep-research` forum run → 12,584-char cited report with 39 web sources

### Caveat

This endpoint is **private/undocumented** and may change with the Antigravity
application. It is used to consume the subscription the user already pays for;
the community documents the same envelope (see the `agycli2api` / `9router`
projects). If Google changes it, fall back to the `gemini-api` backend.

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

> **Superseded the same day.** The free-tier API key could not serve Pro,
> image, or grounding. The final configuration routes everything through the
> **Antigravity application backend** instead (see above) — the LLM records
> are back on the bridge (`provider: open_ai`, url = bridge) with the
> `antigravity-app` backend selected. The account below is retained as the
> diagnostic history that led there.

### How (historical — Gemini API key path)

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
  ├─ @ai_* chat agents ──→ Discourse AI (open_ai provider)
  │                          → bridge /v1/chat/completions
  │                             → antigravity-app backend
  │                                → daily-cloudcode-pa.googleapis.com
  │                                   → Google AI Pro subscription quota
  │
  └─ @deep-research / /deep ──→ discourse-deep-research plugin
                                 → bridge /v1/deep-research (3-phase)
                                    → antigravity-app backend (googleSearch)
```

| Layer | What it is |
|---|---|
| **Google AI Pro subscription** | The only credential. All model, search, and research calls draw from this quota. No Gemini API key, no API prepayment. |
| **Discourse AI agents** | `@ai_*` mentions keep the native Discourse AI agent/mention machinery; the LLM records point at the bridge (`provider: open_ai`). |
| **discourse-deep-research plugin** | Reserved alias `@deep-research …` / `/deep …` → bridge 3-phase research. |
| **bridge `antigravity-app` backend** | Reproduces Antigravity's application request envelope; auto-attaches the `googleSearch` native tool and appends grounding citations. |
| **Deep Research workflow** | plan → gather → synthesize, each phase grounded with Google Search (subscription quota). |

## Quick start

**Forum (recommended)** — point the discourse-ai LLM records at the bridge and
run it with the application backend:

```bash
BRIDGE_TOKEN=change-me \
  ANTIGRAVITY_TOKEN_FILE=~/.gemini/antigravity-cli/antigravity-oauth-token \
  ANTIGRAVITY_CLIENT_SECRET=<secret> \
  python3 bridge/server.py --port 8787 --backend antigravity-app
# → LlmModel records: provider=open_ai, url=http://<bridge>:8787/v1/chat/completions
```

**OAuth sign-in** (once): `python3 demo/auth_agy.py` prints a URL; paste the
callback code back. The token is cached and auto-refreshed.

**Demo CLI**:

```bash
cd demo
python3 cli.py --backend antigravity-app "hello"
```

## Discourse plugin — Google Deep Research

See **[`discourse-deep-research/README.md`](discourse-deep-research/README.md)**
for the complete install guide and
**[`discourse-deep-research/USAGE.md`](discourse-deep-research/USAGE.md)** for
the end-user commands. In short:

```bash
# on the Discourse host:
BRIDGE_TOKEN=change-me ANTIGRAVITY_TOKEN_FILE=… ANTIGRAVITY_CLIENT_SECRET=… \
  python3 bridge/server.py --port 8787 --backend antigravity-app

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

## Available models (Antigravity app backend, verified 2026-08-13)

Model names are the forum slug (`name:` on the LLM record); the bridge aliases
them to the Antigravity application model ids. All calls draw from the Google
AI Pro subscription quota.

| Forum slug | App model | Verified |
|---|---|---|
| `gemini-3.6-flash` | `gemini-3.6-flash-low` | ✅ text, streaming, functionCall, web search |
| `gemini-3.7-flash` | — | ⏳ **released but not yet on the Antigravity app backend** (404 on generateContent, checked 2026-08-13). It IS available on the Gemini API free-tier key, but this forum runs subscription-only — the alias is pre-wired and will light up automatically once the app catalog includes it. |
| `gemini-3.5-flash` | `gemini-3.5-flash-low` | ✅ |
| `gemini-2.5-flash` → `gemini-3.1-flash-lite` | `gemini-3.1-flash-lite` | ✅ (alias; also the web-search-capable model id) |
| `gemini-3.1-pro` | `gemini-3.1-pro-low` | ✅ **Pro on subscription quota** |
| `gemini-2.5-pro` | `gemini-2.5-pro` | ✅ (catalog present) |
| `claude-sonnet-4-6` / `claude-opus-4-6` | `claude-sonnet-4-6` / `claude-opus-4-6-thinking` | ✅ (Antigravity app catalog) |
| `gemini-3.1-flash-image` | `gemini-3.1-flash-image` | ✅ `inlineData` image output |
| `googleSearch` grounding | native tool | ✅ `groundingChunks` + citations |

## Choosing the right mode ⚖️

Three ways to get AI answers in the forum — they use **different knowledge
bases** and are complementary:

| | **Forum Researcher**<br/>(Discourse AI) | **Deep Research**<br/>(`@deep-research`) | **Plain summon**<br/>(`@ai_<model>`) |
|---|---|---|---|
| **Knows** | Your forum's posts only | The web (grounded search) | The current thread + model |
| **Searches** | Posts via `PostsFilter` | Google Search per phase (subscription quota) | Google Search when the model decides (grounding) |
| **Workflow** | understand → plan → dry-run → refine → analyze → summarize | plan → gather → synthesize, each grounded | single LLM call with thread memory |
| **Output** | insights + **citations to forum posts** | structured report + **URL sources** | conversational answer + grounding citations |
| **Best for** | "what have we discussed about X?" | "what does the world say about X?" | "answer my question with web context" |

The Poe-style model picker (`@ai_<model>`) gives the *plain summon* mode with a
choice of models — every reply is tagged with the driving model
(`— ⚙️ 由 <model> 驅動`).

## Feature matrix by model ⚡

Verified live 2026-08-13 on the **Antigravity application backend**
(subscription quota, `googleSearch` auto-attached). ✅ = tested end-to-end.

| Capability | flash (3.6/3.5) | pro (3.1) | image | Notes |
|---|---|---|---|---|
| Text chat | ✅ | ✅ | – | subscription quota |
| Streaming (SSE, OpenAI-compat) | ✅ | ✅ | ✅ | bridge stream verified |
| Multi-turn memory | ✅ | ✅ | – | discourse thread context |
| **Tool calling** (functionDeclarations) | ✅ | ✅ | – | VALIDATED mode |
| **Live web search grounding** (`googleSearch`) | ✅ | ✅ | – | `groundingChunks` → forum citations |
| **Deep Research pipeline** (3-phase) | ✅ | – | – | 12.5k-char report, 39 sources |
| **Image generation** (`inlineData`) | – | – | ✅ | route preserved |
| Thinking (details blocks) | ✅ | ✅ | – | bounded thinkingBudget |
| Topic summaries / AI Helper | ✅ | – | – | via default LLM |

**How to pick a model for discourse-ai agents (current state)**

- Default chat agent → `gemini-3.6-flash` (subscription quota, web search, thinking).
- Summarizer / helper → `gemini-3.5-flash`.
- Legacy 2.5-flash agents → aliased to `gemini-3.1-flash-lite`.
- Pro / image / Claude → available on the Antigravity app backend; enable their
  `@ai_*` agents (currently hidden from the mention list) to surface them.

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

The bridge currently runs the fully-verified `antigravity-app` backend
(subscription quota, web search, image, Pro). The SDK path is no longer the
recommended migration — the application backend already delivers the
subscription experience.

## Honest caveats ⚠️

1. **The subscription does not include the Gemini API.** This project uses the
   Antigravity **application** backend instead — a private, undocumented
   endpoint that can change or be restricted at any time. The `agy` CLI itself
   is official, but automating the application request envelope headlessly is
   outside its documented support envelope.
2. **Google Search is the Antigravity app's native `googleSearch` tool.** It is
   subscription-backed and returns `groundingChunks`, which the bridge turns
   into forum citations — verified with a 39-source deep-research report.
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

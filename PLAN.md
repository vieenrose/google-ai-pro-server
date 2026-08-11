# Plan: OpenAI-Compatible Server → Gemini via Google AI Pro Subscription (Antigravity CLI)

> **Constraint (per user):** the backend must use the **Google AI Pro subscription** —
> **no Gemini API key / no Gemini API billing**. This rules out the official
> Gemini API (including the paid Deep Research Interactions API) as a backend.

## 1. What this means, verified against current Google docs (2026)

### 1.1 The subscription cannot drive the Gemini API directly

- Google AI Pro / Ultra are **consumer** subscriptions (Gemini app, NotebookLM,
  Antigravity, AI Studio *UI* quotas).
- Gemini API usage is billed **separately**; Deep Research agent access via the
  API *"requires a paid API key"* and is *"not included in Google AI plans"*.
- ⇒ We cannot call `generativelanguage.googleapis.com` with the subscription.

### 1.2 The working subscription path: Antigravity CLI (`agy`)

Google replaced Gemini CLI with **Antigravity CLI** for Google One / unpaid users
(June 2026). It signs in with your **Google account** (the AI Pro account) via
browser OAuth, caches credentials in `~/.gemini/antigravity-cli/`, and uses the
same backend as the Gemini app — **no API key, no billing**.

**It has a headless (print) mode**, which is exactly what we need to script:

```bash
agy -p "In one sentence, what is a git rebase?"
# → text to stdout; diagnostics to stderr

agy -p "…" --output-format json | jq
# → { conversation_id, status, response, error, duration_seconds,
#     num_turns, structured_output, json_schema, usage{input,output,thinking,…} }

agy -p "…" --output-format stream-json
# → NDJSON: init → step_update{text_delta, tool_info, subagent_info, usage} → result
```

Flags we rely on: `-p/--prompt`, `--output-format text|json|stream-json`,
`--json-schema`, `--model`, `--agent`, `--dangerously-skip-permissions`
(automation), `conversation_id` resume for multi-turn.

### 1.3 Models available on AI Pro (from Antigravity docs)

| Model | Pro plan |
|---|---|
| Gemini 3.6 Flash (Low/Med/High) | ✅ |
| Gemini 3.5 Flash (Low/Med/High) | ✅ |
| Gemini 3.1 Pro (Low/High) | ✅ |
| Claude Sonnet 4.6 (thinking) | ✅ |
| Claude Opus 4.6 (thinking) | ✅ |
| GPT-OSS 120B | ✅ |
| Nano Banana 2 (image gen, internal) | ✅ |

### 1.4 Deep Research — honest positioning ⚠️

- **The true Gemini Deep Research agent is NOT available through the
  subscription.** It exists only (a) in the Gemini **app** (included with AI Pro,
  not scriptable) and (b) as a **paid** API (Interactions API).
- **What the subscription gives us instead:** the Antigravity agent — an agentic
  model with **Google Search grounding, web fetch, code execution, subagents,
  MCP servers**. We can drive it as a *research agent*:
  prompt it to plan → search → read → synthesize with citations, i.e. a
  **Deep-Research-style workflow on the subscription backend**.
- The server should expose `deep-research` / `deep-research-max` as **modes that
  run this research workflow**, and clearly label output quality expectations.
- **Future upgrade path (optional):** pluggable backend — if the user ever
  obtains a paid key, swap the Deep Research backend to the official
  Interactions API with no client-visible change.

---

## 2. Architecture

```
Client (any OpenAI SDK / curl / LLM tool)
   │  baseURL = http://localhost:8787/v1   api_key = anything
   ▼
┌──────────────────────────────────────────────────────────────┐
│ OUR SERVER  (Node.js 22, zero runtime deps)                  │
│                                                              │
│  POST /v1/chat/completions  (sync + stream)                  │
│  GET  /v1/models                                             │
│  GET  /health                                                │
│                                                              │
│  Router:                                                     │
│    model == deep-research*       → research workflow mode    │
│    model == gemini-*/claude-*/…  → chat mode                 │
│                                                              │
│  Chat:  OpenAI messages → prompt builder → spawn agy headless│
│         (--output-format json | stream-json)                 │
│         → translate envelope/NDJSON → OpenAI response / SSE  │
│                                                              │
│  Research: multi-phase agy runs (plan → search → synthesize) │
│            + progress stream + citations                     │
└───────┬───────────────────────────────────────────┬──────────┘
        │  child processes (agy, per request)       │
        ▼                                           ▼
┌───────────────────────────┐        ┌───────────────────────────────┐
│ agy chat (subscription)   │        │ agy research workflow         │
│ Gemini 3.6/3.5 Flash,     │        │ grounded search + subagents   │
│ Gemini 3.1 Pro, Claude,   │        │ ≈ Deep Research (approx.)     │
│ GPT-OSS-120b              │        └───────────────────────────────┘
└───────────────────────────┘
        OAuth creds from ~/.gemini/antigravity-cli/ (AI Pro account)
```

---

## 3. Why shell out to `agy` instead of calling the internal API directly

The community has reverse-engineered the subscription backend
(`https://cloudcode-pa.googleapis.com` using the OAuth token from
`~/.gemini/antigravity-cli/antigravity-oauth-token`) — see
`usamashehab/antigravity-proxy`, `frieser/antigravity-proxy`,
`liuw1535/antigravity2api-nodejs`.

**Decision: use the official CLI (agy) as the primary backend.**

| | `agy` headless (primary) | Direct internal API (community proxies) |
|---|---|---|
| Stability | Official, versioned | Unofficial, breaks silently |
| Features | models, tools, structured output, NDJSON streaming | models only, per-proxy |
| Effort | spawn + parse | reimplement RPC protocol |
| Risk | low | ToS gray area, brittle |

Fallback: if `agy` headless is throttled/removed, we can swap the transport to
the OAuth-token + cloudcode-pa approach (both use the same credentials).

---

## 4. Technology choices

| Decision | Choice |
|---|---|
| Runtime | Node.js 22 (installed), ESM |
| Framework | `node:http` (no framework) — proxy + child-process orchestration |
| Deps | zero runtime deps |
| Child process | `child_process.spawn` per request; small worker pool (see §6) |
| Config | `.env` via `node --env-file` |
| Tests | `node:test` + `scripts/smoke.mjs` |

---

## 5. API surface

| Endpoint | MVP | Notes |
|---|---|---|
| `GET /v1/models` | ✅ | Models from §1.3 + `deep-research*` aliases |
| `POST /v1/chat/completions` sync | ✅ | chat mode (json envelope) |
| `POST /v1/chat/completions` stream | ✅ | chat + research mode (stream-json → SSE) |
| `GET /health` | ✅ | also reports agy auth status |
| `POST /v1/embeddings` | later | not available via agy; skip or note |
| `POST /v1/responses` | later | map to chat |

---

## 6. Translation details

### 6.1 Chat mode (OpenAI → agy → OpenAI)

1. **Prompt builder** — OpenAI `messages[]` → one prompt string:
   - system messages → `System: …` prefix
   - tool results / history → compact transcript
   - last user message (text; images currently unsupported → explicit 400 with
     explanation, or attempt `image_url` → describe via Nano Banana? out of scope v1)
2. **Sync:** `spawn agy -p <prompt> --output-format json --model <m>`
   → parse envelope → OpenAI `chat.completion`:
   - `content = result.response`
   - `usage = result.usage` (map `output_tokens`→`completion_tokens`,
     `input_tokens`→`prompt_tokens`)
   - `model` = client-facing name
3. **Stream:** `--output-format stream-json` → for each NDJSON line:
   - `step_update` with `text_delta` → SSE chunk `{ choices:[{ delta:{ content } }] }`
   - tool steps → optionally surface as `delta.tool_calls` or ignore in v1
   - `result` → final chunk + `[DONE]`
4. **Errors:** non-zero exit / `error` field / auth-required exit → OpenAI-shaped
   `{ error: { message, type, code } }` (429-style when quota exhausted).

### 6.2 Multi-turn

- Keep a server-side `conversation_id → (model, conversation_id)` map.
- Subsequent requests in the same OpenAI `conversation`/session reuse the agy
  `conversation_id` (resume) instead of resending history — cheaper & coherent.

### 6.3 Deep Research mode (research workflow)

`model = deep-research` / `deep-research-max` triggers a **multi-phase agy run**:

1. **Phase 0 – Plan:** `agy -p "Create a research plan for: <topic>"` →
   extract numbered research questions.
2. **Phase 1 – Gather:** for each question, `agy -p "<question> — use web search,
   read sources, take notes with citations"` (grounding + URL context tools).
3. **Phase 2 – Synthesize:** single `agy` call with all notes →
   structured report (request Markdown with citations via prompt).
4. **Output:** report as `content`; citations appended as `## Sources`; progress
   emitted as SSE `step_update`-style deltas when streaming.

Cost controls: cap phases/questions (`MAX_RESEARCH_QUESTIONS`, default 3),
enforce concurrency limit, honor `/quota` (AI Pro daily quotas).

### 6.4 Concurrency & quotas

- AI Pro gives *daily* quota in Antigravity (reset-based). Add:
  - `MAX_CONCURRENT_AGY` worker pool (default 2)
  - request queue with timeout
  - `GET /quota` passthrough via `agy` status
- Log per-request duration + token usage to JSONL.

---

## 7. Auth & config

- **Client:** `Authorization: Bearer <anything>` — server doesn't validate (it's
  your own server); optional `CLIENT_KEY` check via env.
- **Backend:** nothing to configure except making sure `agy` is signed in once:
  ```
  curl -fsSL https://antigravity.google/cli/install.sh | bash   # installs agy
  agy   # one-time interactive sign-in with the AI Pro Google account
  ```
- `.env`:
  ```
  PORT=8787
  HOST=127.0.0.1
  CLIENT_KEY=            # optional gate
  AGY_BIN=agy
  DEFAULT_MODEL=gemini-3.5-flash
  MAX_CONCURRENT_AGY=2
  MAX_RESEARCH_QUESTIONS=3
  RESEARCH_AGENT_PROMPT_FILE=./research-agent.md   # workflow prompt
  ```

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Antigravity CLI is young (v1.1.x), APIs may shift | Pin version; isolate agy calls behind a `transport.js` module |
| AI Pro daily quotas limit headless use | Concurrency pool, queue, quota endpoint, clear 429-style errors |
| Headless/agentic use may be restricted later | Keep direct cloudcode-pa token path as documented fallback |
| No real Deep Research agent on subscription | Research workflow approximates it; document quality expectations; pluggable paid backend later |
| Google ToS for automated use | Personal-use local server only; no resale; review terms |

---

## 9. Testing strategy

1. **Unit:** prompt builder, envelope→OpenAI mapper, NDJSON→SSE mapper, model alias table.
2. **Integration (needs signed-in `agy`):**
   - sync + stream chat with `gemini-3.5-flash`
   - multi-turn (conversation resume)
   - `deep-research` mode on a small topic (2 questions) — verify citations
   - error paths: agy not authenticated, quota exhausted, unknown model
3. **Client conformance:** `openai` npm SDK against `http://127.0.0.1:8787/v1`;
   curl SSE check.
4. `scripts/smoke.mjs` runs all above.

---

## 10. Milestones

| # | Scope | Done when |
|---|---|---|
| M0 | Scaffold: server, /health, /v1/models, agy auth check | server boots; `agy` detected + signed in |
| M1 | Chat sync: OpenAI → agy json envelope → OpenAI response | curl chat works |
| M2 | Chat stream: stream-json → SSE; multi-turn resume | openai SDK streaming works |
| M3 | Deep Research mode: 3-phase workflow + citations | deep-research request returns report |
| M4 | Hardening: pool/queue, quota endpoint, error mapping, logging, README | smoke script green |
| M5 | Optional: embeddings placeholder, /v1/responses, paid-backend plug-in for real Deep Research | per-feature |

---

## 11. Open questions

1. Is `agy` already installed / signed in on your machine? (one-time `agy`
   browser sign-in needed with the AI Pro Google account)
2. Preferred default model — `gemini-3.5-flash` (fast) or `gemini-3.1-pro` (strong)?
3. For `deep-research`, OK with the *approximation* workflow (plan→search→
   synthesize), or should we also wire an optional paid-API backend for the real
   agent (best of both)?
4. Port/binding: local only (`127.0.0.1:8787`) or exposed on LAN?
5. Scope for v1: M0–M3 (chat + research), or straight to M4 (hardened)?

---

## 12. References

- Antigravity CLI docs: https://antigravity.google/docs/cli/overview
  - Headless mode: https://antigravity.google/docs/cli/headless
  - Models: https://antigravity.google/docs/models
  - Subagents: https://antigravity.google/docs/cli/subagents
  - Gemini migration: https://antigravity.google/docs/cli/gcli-migration
- Antigravity CLI repo: https://github.com/google-antigravity/antigravity-cli
- Gemini Deep Research agent (paid API only): https://ai.google.dev/gemini-api/docs/deep-research
- Google AI plans (API access not included): https://ai.google.dev/gemini-api/docs/google-ai-plans
- Community OAuth-token proxies (fallback transport):
  - https://github.com/usamashehab/antigravity-proxy
  - https://github.com/frieser/antigravity-proxy

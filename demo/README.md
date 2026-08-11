# Gemini AI Pro demo — chat + Deep Research CLI

A zero-dependency Python CLI that lets you **chat with Gemini** and run
**Deep Research**, powered entirely by your **Google AI Pro subscription** —
no Gemini API key, no API billing.

```
you> hello
gemini> …
you> /deep What are the latest trends in solid-state batteries?
  ▸ Phase 1/3 — planning research on: …
  ▸ Phase 2/3 — researching question 1/3: …
  ▸ Phase 3/3 — synthesizing the final report…
✔ Report complete — 3 question(s), 12 source(s), 154s
```

## Status: ✅ live-verified (2026-08-11)

Tested end-to-end on a real Google AI Pro account. **Authentic Google Search
works** — the `agy` backend runs the full agent (same grounding the Gemini
app uses):

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

The reports are grounded in real search results with citations
(`grounding-api-redirect` links, same as the Gemini app).

Setup in one minute:
```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash  # installs agy
agy                                                          # interactive sign-in
# when agy shows "paste the authorization code here":
#   1. browser opens → sign in with your AI Pro Google account
#   2. copy the code from the redirect URL
#   3. paste it INTO the agy terminal (not elsewhere) and press Enter
```

## How it consumes the subscription

| Layer | What it is |
|---|---|
| **Antigravity CLI (`agy`)** | Google's official CLI (successor of Gemini CLI). Signs in with your **Google account** (the AI Pro one) via browser OAuth once, then runs headless forever. |
| **Headless mode** | `agy -p "…" --output-format json \| stream-json` — scriptable, machine-readable output, uses cached credentials. |
| **Direct backend (optional)** | Talks to Google's Antigravity/Cloud Code Assist backend (`cloudcode-pa.googleapis.com`) using the token `agy` cached — the technique from the open-source community proxy (recycled, MIT-licensed). |

> ⚠️ **Deep Research honesty check:** the *true* Gemini Deep Research agent is
> only available in the Gemini app (not scriptable) or as a **paid** Gemini API
> (Interactions API). The subscription gives us the agentic Gemini with Google
> Search grounding instead, so this CLI runs a **research workflow**
> (plan → grounded search → cited report) that approximates Deep Research.
> If you ever get a paid key, swap `deep_research.py` for the official
> Interactions API — the CLI doesn't change.

## Setup (one time, on your machine)

```bash
# 1. Install Antigravity CLI
curl -fsSL https://antigravity.google/cli/install.sh | bash

# 2. Sign in with your Google AI Pro account (opens a browser)
agy

# 3. Done. The demo reuses this login; no API key needed.
```

## Usage

```bash
python3 cli.py "hello"                                     # one-shot chat
python3 cli.py --deep "quantum computing trends 2026"       # deep research
python3 cli.py --model gemini-3.1-pro "explain async/await" # pick a model
python3 cli.py                                             # interactive REPL
python3 cli.py --backend mock "hello"                       # offline demo
```

### REPL commands

| Command | What it does |
|---|---|
| *(type a message)* | chat with Gemini |
| `/deep <topic>` | run the Deep Research workflow (plan → search → report) |
| `/model <name>` | switch model: `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.1-pro`, `gemini-3.1-pro-high`, `gemini-2.5-pro`, `claude-sonnet-4.6`, `claude-opus-4.6`, `gpt-oss-120b` |
| `/backend <name>` | switch backend: `agy`, `direct`, `gemini`, `mock` |
| `/stream` | toggle streaming output |
| `/status` | show active backend + model |
| `/help`, `/quit` | help / exit |

Backends are auto-selected in order: `agy` → `direct` → `gemini` → `mock`.

## Files

```
demo/
├── cli.py               # CLI entry point (REPL + one-shot)
├── gemini_backends.py   # pluggable backends (agy / direct / gemini / mock)
└── deep_research.py     # 3-phase Deep Research workflow
```

## Troubleshooting

- **`authentication timed out`** → run `agy` once interactively and sign in
  with the AI Pro Google account.
- **`binary not found: agy`** → install with the setup command above.
- **Quota errors** → AI Pro has daily quotas in Antigravity; wait or use
  `/model gemini-3.5-flash` (cheaper model).

## Next step: Discourse plugin

This CLI is step 1. The plan (see `../PLAN.md`) is a Discourse plugin that lets
forum users summon Gemini into a topic or trigger Deep Research — the plugin
will call this same backend logic (via a small HTTP bridge or the Python module
directly).

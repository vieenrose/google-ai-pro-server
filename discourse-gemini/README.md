# discourse-gemini — summon Gemini on your forum

A Discourse plugin that lets forum users:

- **`@gemini <message>`** — summon Gemini into the topic; the bot posts a reply
  (chat, search-grounded, powered by your Google AI Pro subscription)
- **`/deep <topic>`** — trigger Gemini Deep Research; the bot posts a cited
  research report a few minutes later

**No Gemini API key.** It consumes your Google AI Pro subscription through the
local bridge → Antigravity CLI (`agy`).

```
User post:  @gemini what's the latest news about AI?
        ↓
Discourse Sidekiq job → bridge (localhost:8787) → agy (AI Pro OAuth)
        ↓
Bot post:  🤖 Gemini … (real, search-grounded answer)
```

## Requirements

- Discourse (tested target: recent stable)
- The bridge running on the Discourse host: `python3 bridge/server.py --port 8787`
  (see `bridge/README.md`)
- `agy` installed + signed in once with the AI Pro Google account
  (`curl -fsSL https://antigravity.google/cli/install.sh | bash && agy`)

## Install

```bash
cd /var/discourse/plugins
git clone https://github.com/vieenrose/google-ai-pro-server.git discourse-gemini
# or copy the discourse-gemini/ directory into your plugins dir
cd /var/discourse && ./launcher rebuild app
```

Then in **Admin → Settings → Plugins → Discourse Gemini**:
- `gemini bridge url` → `http://127.0.0.1:8787`
- `gemini bridge token` → same value as the bridge's `BRIDGE_TOKEN` (or empty)
- `gemini allowed groups` → e.g. `trust_level_2`
- `gemini daily limit per user` → per-user daily cap

The bot user (`gemini`) is created automatically on first boot.

## Usage

| Post content | Result |
|---|---|
| `@gemini explain async/await` | Gemini replies in-thread |
| `/deep fusion energy 2026` | Gemini posts a cited research report (1–5 min) |

Permission & rate limits are enforced per-user via the settings above.

## Layout

```
discourse-gemini/
├── plugin.rb                       # plugin entry, triggers, bot user
├── config/
│   ├── settings.yml                # plugin settings
│   └── locales/en.yml              # strings
├── lib/gemini_bridge.rb            # HTTP client for the local bridge
├── jobs/regular/
│   ├── gemini_reply.rb             # chat reply job
│   ├── gemini_deep_research.rb     # deep research job
│   └── gemini_notice.rb            # permission/rate-limit notices
└── assets/stylesheets/gemini.scss  # bot post styling
```

## Security notes

- The bridge should listen on `127.0.0.1` only; set `BRIDGE_TOKEN` and mirror
  it in `gemini bridge token`.
- `/deep` costs real AI Pro quota and takes minutes — the per-user daily limit
  is important; raise it carefully.
- This automates a consumer subscription — keep it for personal/community use,
  review Google's terms before scaling.

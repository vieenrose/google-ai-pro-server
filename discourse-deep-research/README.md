# Google Deep Research for Discourse

Deep Research for your self-hosted Discourse forum, powered by your
**Google AI Pro subscription**. Users trigger a research run in any topic
with a simple mention and get back a structured, sourced report.

```
@deep-research 請研究目前有哪些語音聊天機器人網站 DEMO 的成功案例
```

or the slash form:

```
/deep 請研究目前有哪些語音聊天機器人網站 DEMO 的成功案例
```

## What it does

1. A `deep-research` bot user acknowledges the request
2. The **local bridge** (`bridge/server.py`) runs the research pipeline:
   - breaks the topic into sub-questions (`gemini_deep_research_max_questions`)
   - verifies each one with real model answers
   - synthesizes a report **with sources**
3. The bot posts the finished report into the topic

Runs take 1–5 minutes (it performs multiple model calls).

## Architecture

```
post "@deep-research …"
   → plugin on(:post_created) hook
   → Jobs::GeminiDeepResearch (sidekiq)
   → GeminiBridge (HTTP) → bridge /v1/deep-research → Google AI Pro quota
   → report posted by the deep-research bot user
```

The plugin itself only speaks HTTP to the local bridge — the bridge owns the
model configuration and credentials.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `gemini_enabled` | ✅ on | plugin master switch |
| `gemini_deep_research_enabled` | ✅ on | @deep-research / /deep |
| `gemini_allowed_groups` | tl1–tl4 | who may trigger research |
| `gemini_daily_limit_per_user` | 20 | shared per-user daily cap |
| `gemini_deep_research_max_questions` | 3 | sub-questions per run |
| `gemini_bridge_url` | `http://127.0.0.1:8787` | bridge base URL |
| `gemini_bridge_token` | — | bridge bearer token |
| `gemini_model` | `gemini-3.5-flash` | research model (bridge slug) |

## Migration from `discourse-gemini`

This plugin was previously named **discourse-gemini** (version 0.1.0) and also
handled a `@gemini` chat feature. Version 0.2.0 removes `@gemini` — chat is
covered by Discourse AI's native agents (`@ai_gemini_3_6_flash`, …), which now
run on the official Gemini API path with the AI Pro subscription. See the
repository README ("Native Discourse AI migration") for the full story.

**Internal identifiers were deliberately kept** (setting keys `gemini_*`,
module `DiscourseGemini`, job names, the `discourse-gemini-bot-post` CSS
class) so existing forum data, stored settings, and already-posted reports
survive the rename untouched. Deploy by replacing the old plugin directory
with this one; no database migration is needed.

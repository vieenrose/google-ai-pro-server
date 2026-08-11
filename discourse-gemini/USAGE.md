# Using Gemini Deep Research in Discourse

The plugin gives forum users two commands. This is how a member actually uses
them, what they see, and what to check when it doesn't work.

## The two commands

| You post… | What happens |
|---|---|
| `@gemini explain the difference between HTTP/2 and HTTP/3` | Gemini replies **in the topic within ~10–30 s** with a chat answer (search-grounded) |
| `/deep fusion energy progress 2026` | Gemini posts **"Deep Research started…"**, then a **cited research report a few minutes later** |

### Rules of thumb

- **The command must be the ONLY thing in the post** — first line, nothing else.
  (e.g. a post containing `@gemini hi` plus other text is ignored.)
- `@gemini` = chat. `/deep` = deep research (plan → live web search → cited report).
- Both are limited per user per day (`gemini daily limit per user`, default 20).
- Only users in `gemini allowed groups` can use them.

## What a `/deep` session looks like

```
luigi  ·  12:01
/deep What are the latest solid-state battery breakthroughs?

gemini  ·  12:01
🧪 Deep Research started — this takes a few minutes. Gemini will post the
report here when it's done.

gemini  ·  12:04
# 📚 Gemini Deep Research: What are the latest solid-state battery breakthroughs?

## 1. Commercialization milestones
… [1][2]

## Sources (17)
[1] https://…
[2] https://…
…
```

The report is real, **search-grounded** content (same Google grounding the
Gemini app uses) with inline citations and a source list.

## What a `@gemini` chat looks like

```
luigi  ·  14:20
@gemini what's the latest news about AI this week?

gemini  ·  14:20
🤖 Gemini

Here's what happened in AI this week…
(grounded answer with sources)
```

## Setting it up (one time, on the Discourse host)

```bash
# 1. Install Antigravity CLI + sign in once with your AI Pro Google account
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy
#    (when it shows "paste the authorization code here":
#     browser opens → sign in → copy code → paste INTO the agy terminal → Enter)

# 2. Run the bridge (keep it alive: systemd/nohup/tmux)
export BRIDGE_TOKEN='change-me'
nohup python3 /path/to/repo/bridge/server.py --port 8787 --backend agy \
  >> /var/log/gemini-bridge.log 2>&1 &

# 3. Install the plugin
cd /var/discourse/plugins
git clone https://github.com/vieenrose/google-ai-pro-server.git discourse-gemini
cd /var/discourse && ./launcher rebuild app

# 4. Configure (Admin → Settings → Plugins → Discourse Gemini)
#    gemini bridge url        = http://127.0.0.1:8787
#    gemini bridge token      = change-me          (same as BRIDGE_TOKEN)
#    gemini allowed groups    = trust_level_1      (or your group names)
```

> ℹ️ The bridge must run on a machine the **Discourse server** can reach.
> If Discourse is in a container, `127.0.0.1` refers to the container — use the
> host IP (e.g. `http://172.17.0.1:8787`) or run the bridge inside the container.

## Troubleshooting

| Symptom | Check |
|---|---|
| No bot reply at all | Plugin enabled? Bridge running? `curl http://127.0.0.1:8787/health` returns `ok: true`? Token matches `BRIDGE_TOKEN`? |
| `⚠️ You don't have permission…` | User's group not in `gemini allowed groups` |
| `⚠️ daily limit` | Wait for reset or raise `gemini daily limit per user` |
| `⚠️ Gemini hit an error…` | Bridge log (`/var/log/gemini-bridge.log`); usually agy auth expired → re-run `agy` |
| Bot posts nothing after `Deep Research started…` | Deep research takes 1–5 min; check Sidekiq `jobs/regular/gemini_deep_research.rb` + bridge log |

## Verify the bridge quickly (before touching Discourse)

```bash
curl -X POST http://127.0.0.1:8787/v1/chat \
  -H "Authorization: Bearer change-me" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"say hi"}]}'

curl -X POST http://127.0.0.1:8787/v1/deep-research \
  -H "Authorization: Bearer change-me" -H "Content-Type: application/json" \
  -d '{"topic":"AI news this week","max_questions":1}'   # takes ~1 min
```

# discourse-gemini

Let forum users **summon Gemini** (`@gemini …`) and run **Deep Research**
(`/deep …`) — powered by your **Google AI Pro subscription** via the local
bridge → Antigravity CLI. **No Gemini API key, no API billing.**

```
User post:  @gemini what's the latest AI news?
        ↓  (Discourse event → Sidekiq job)
GeminiBridge (Ruby HTTP client)
        ↓  localhost:8787
bridge/server.py (Python)
        ↓  agy (Antigravity CLI, AI Pro OAuth)
real Gemini — chat with authentic Google Search grounding / cited research reports
```

- ✅ Live-verified: chat replies and search-grounded research reports
- ✅ Bot user auto-created, permission groups, per-user daily limits
- ✅ Zero runtime dependencies on the Discourse side

---

## 1. Requirements

| Requirement | Notes |
|---|---|
| Discourse | Any recent stable version (tested target: stable 2026) |
| Python 3.10+ on the Discourse host | for the bridge |
| Antigravity CLI (`agy`) | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |
| Google AI Pro subscription | the account you sign into `agy` |
| Ruby / Bundler | ships with Discourse; nothing extra |

---

## 2. Install — step by step

All commands run **on the Discourse host** (or a host the Discourse server can
reach on localhost).

### 2.1 Install & sign in the Antigravity CLI (one time)

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy
```

`agy` opens a browser. **Sign in with your Google AI Pro account**, then when
the terminal shows *"paste the authorization code here"*: copy the code from
the browser's redirect URL and **paste it into the agy terminal** (not anywhere
else), press Enter. You should see `status: SUCCESS`.

Sanity check (headless, no browser needed):

```bash
agy -p "Reply with exactly: ok" --output-format json --dangerously-skip-permissions
```

### 2.2 Run the bridge

```bash
git clone https://github.com/vieenrose/google-ai-pro-server.git /opt/google-ai-pro-server
export BRIDGE_TOKEN='change-me'          # pick a strong value
python3 /opt/google-ai-pro-server/bridge/server.py --port 8787 --backend agy
```

Keep it running (nohup/tmux, or systemd — see `bridge/gemini-bridge.service`).
Verify:

```bash
curl http://127.0.0.1:8787/health   # → {"ok": true, "backend": "agy", ...}
```

### 2.3 Install the plugin

```bash
cd /var/discourse/plugins
git clone https://github.com/vieenrose/google-ai-pro-server.git discourse-gemini
cd /var/discourse
./launcher rebuild app
```

(If you already cloned the repo elsewhere, symlink or copy the
`discourse-gemini/` directory instead.)

### 2.4 Configure (Admin → Settings → Plugins → **Discourse Gemini**)

| Setting | Default | What it does |
|---|---|---|
| `gemini enabled` | ✅ | Master switch |
| `gemini bridge url` | `http://127.0.0.1:8787` | Where the bridge listens. ⚠ Containerized Discourse: `127.0.0.1` is the container — use `http://<host-ip>:8787` |
| `gemini bridge token` | *(empty)* | Must match the bridge's `BRIDGE_TOKEN` |
| `gemini allowed groups` | `trust_level_1\|…\|trust_level_4` | Pipe-separated groups allowed to summon Gemini |
| `gemini bot username` | `gemini` | Bot account that posts replies (auto-created) |
| `gemini model` | `gemini-3.5-flash` | Model used for chat |
| `gemini daily limit per user` | `20` | Per-user daily call cap (chat + deep share it) |
| `gemini deep research enabled` | ✅ | Allow `/deep` |
| `gemini deep research max questions` | `3` | Sub-questions the research explores |

---

## 3. Usage

### Chat — `@gemini`

Create a post whose **entire content** is:

```
@gemini explain the difference between HTTP/2 and HTTP/3
```

The `gemini` bot replies in-thread within ~10–30 s with a search-grounded
answer.

### Deep Research — `/deep`

Create a post whose **entire content** is:

```
/deep solid-state battery breakthroughs 2026
```

The bot first posts *"🧪 Deep Research started…"*, then, **1–5 minutes later**,
a cited research report:

```
# 📚 Gemini Deep Research: solid-state battery breakthroughs 2026

## 1. Commercialization milestones
… [1][2]

## Sources (17)
[1] https://…
```

The report is generated with **live Google Search grounding** (the same
grounding the Gemini app uses) — not from memory.

### Rules

- The command must be **the only content of the post** (first line, nothing
  after).
- Both commands share the per-user daily limit.
- Only users in `gemini allowed groups` can use them.
- Replies are posted by the `gemini` bot (or `Discourse.system_user` if the bot
  account can't be created).

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| No bot reply at all | Is `gemini enabled` ✅? Is the bridge up (`curl http://127.0.0.1:8787/health`)? Do the bridge token and plugin setting match? Is Sidekiq running? |
| `⚠️ You don't have permission…` | User's group missing from `gemini allowed groups` |
| `⚠️ daily limit` | Per-user cap reached; raise `gemini daily limit per user` or wait for reset |
| `⚠️ Gemini hit an error…` | Check the bridge log. Most common: agy auth expired → re-run `agy` interactively |
| Bot never posts the deep-research report | Research takes 1–5 min; check Sidekiq and the bridge log |
| Bridge unreachable from Discourse | Containerized Discourse → use the host IP, not `127.0.0.1` |
| `UnknownAttributeError … bot` | Cosmetic; the plugin handles older/newer Discourse versions |

---

## 5. Layout

```
discourse-gemini/
├── plugin.rb                       # entry, bot user, triggers, permissions, limits
├── config/
│   ├── settings.yml                # plugin settings
│   └── locales/en.yml              # UI strings
├── lib/gemini_bridge.rb            # Ruby HTTP client for the bridge
├── jobs/regular/
│   ├── gemini_reply.rb             # @gemini chat job
│   ├── gemini_deep_research.rb     # /deep job (posts started-notice + report)
│   └── gemini_notice.rb            # permission / rate-limit notices
├── assets/stylesheets/gemini.scss  # bot post styling
├── README.md                       # this file
└── USAGE.md                        # end-user command guide
```

## 6. Security & operations

- **Bind the bridge to `127.0.0.1`** and set a `BRIDGE_TOKEN`; mirror it in the
  plugin settings. Never expose the bridge publicly without auth.
- `/deep` consumes real AI Pro quota and takes minutes — keep the per-user
  daily limit sensible.
- This automates a **consumer subscription** for personal/community use; review
  Google's terms before scaling or reselling.
- The bot user can't post to private categories unless it has access — grant it
  category permissions if you want Gemini in private topics.

# Google Deep Research plugin — usage

## For forum users

In any topic, post a message that starts with the mention or the slash
command:

```
@deep-research <your research question>
```

```
/deep <your research question>
```

The `deep-research` bot replies first with a "started" notice, then posts the
full report (sub-questions → findings → conclusion → sources) a few minutes
later.

## For the admin

### Prerequisites

1. **Bridge** running and reachable from the Discourse container:
   ```bash
   # on the host
   sudo systemctl status gemini-bridge
   # health check
   curl -H "Authorization: Bearer $BRIDGE_TOKEN" http://127.0.0.1:8787/health
   ```
   From inside the Discourse container the URL is `http://172.17.0.1:8787`.

2. **Settings** in Discourse admin (search for `gemini_`):
   - `gemini_enabled` = true
   - `gemini_bridge_url` = the bridge URL the container can reach
   - `gemini_bridge_token` = the bridge's `BRIDGE_TOKEN`
   - `gemini_allowed_groups` / `gemini_daily_limit_per_user` per your policy

### Install / upgrade

Copy this directory into the Discourse container's `plugins/` directory as
`discourse-deep-research` (replacing the old `discourse-gemini` if present),
then rebuild or restart the container:

```bash
./launcher rebuild app   # full rebuild (bakes the plugin)
# or, for a quick dev cycle: copy into the running container and restart
```

For the launcher-style install used by this project, the plugin is baked via
the `app.yml` hooks at bootstrap.

### Troubleshooting

| Symptom | Check |
|---|---|
| No reply at all | `gemini_enabled` on? plugin dir present? `/var/www/discourse/plugins/` |
| "not allowed" notice | `gemini_allowed_groups` contains the user's group? |
| "rate limited" notice | daily cap reached — `gemini_daily_limit_per_user` |
| Error notice with bridge error | bridge health + token; bridge journal `journalctl -u gemini-bridge` |
| Report posts but empty sections | `gemini_model` slug valid for the bridge backend |

### Deep research vs Discourse AI agents

`@deep-research` runs the **bridge's multi-phase research pipeline** (multiple
model calls, sub-questions, sources). If you just want a knowledgeable answer
with forum context, the native Discourse AI agents
(`@ai_gemini_3_6_flash`, `@forum_researcher_bot`, …) handle that and now run
on the official Gemini API with the same Google AI Pro subscription.

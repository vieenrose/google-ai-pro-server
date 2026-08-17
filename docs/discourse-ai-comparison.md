# Sloth AI Plugin vs Discourse AI — Architecture Comparison

Date: 2026-08-17
Source: study of `/admin/plugins/discourse-ai/*` (ai-llms, ai-agents, ai-secrets,
ai-tools, ai-usage) vs the Sloth AI plugin (discourse-deep-research).

## 1. Overview

| Dimension | Discourse AI (official) | Sloth AI (ours) |
|---|---|---|
| Scope | General-purpose AI framework (agents, moderation, translation, embeddings, summarization, bots) | Forum AI bots + quota monitor + LDR research bridge |
| Model registry | `llm_models` table — CRUD UI per model (provider, url, tokenizer, costs, vision) | No DB registry — models hardcoded in bridge routing (`OPENCODE_PREFIXES`, `TOGETHER_PREFIXES`) + bot JSON map |
| Secrets | `ai_secrets` table — named secrets with `********` masking, `used_by` tracking, referenced by FK from models/tools | Site settings (`gemini_opencode_api_key`, `gemini_together_api_key`, secret type) pushed to bridge key files |
| Agents | `ai_agents` table + full agent framework (system prompt, tools, RAG, allowed groups, temperature, streaming) | Bot users (`ai_*`) mapped 1:1 to models; smolagents CodeAgent subprocess for grounding |
| Tools | `ai_tools` table + MCP servers + secret bindings + per-tool test UI | Only web_search (SearXNG) baked into smol agent |
| Cost tracking | `ai_api_request_stats`, `ai_usage` report, per-model input/output cost, `llm_quotas` (group token/cost caps) | None (only OpenCode rolling/weekly/monthly quota % display) |
| Providers | 16 providers (open_ai, anthropic, gemini, vertex, bedrock, ollama, mistral, cohere, open_router, groq, vllm, …) via endpoint classes | 3 backends: Antigravity (Gemini), OpenCode Go, Together AI |
| Admin UI | One Ember admin page (`/admin/plugins/discourse-ai`) with tabs: AI Agents, LLMs, Tools, Secrets, Usage, Quotas | Server-rendered page (`/admin/plugins/sloth-ai`) + native site settings |
| Re-auth | N/A (uses API keys/secrets) | Antigravity OAuth PKCE re-auth flow (2-step, in admin) |
| Quota display | `llm_quotas` = *limits you set* per group; usage report = *actual spend* | Token pool grouping from provider quota APIs (Antigravity remaining%, OpenCode usage %) |

## 2. Key architectural ideas in Discourse AI worth adopting

### 2.1 Model registry (`llm_models`)
- DB rows: `display_name, name, provider, url, api_key/ai_secret_id, tokenizer,`
  `max_prompt_tokens, max_output_tokens, input_cost, output_cost, vision_enabled,`
  `provider_params (jsonb), enabled_chat_bot`
- Admin CRUD UI with a **Test** button per model; providers listed from
  `Llm.provider_names` with capability metadata.
- **Gap in Sloth AI**: no model registry. Adding a provider/model = code change.
  A registry table (or even a JSON site setting) would let admins add/remove
  models without deploys. Bridge routing could consult the registry.

### 2.2 Secrets (`ai_secrets`)
- Named secrets, `********` in list, `unmask` only in `show` (admin-only scope).
- `used_by` computed (llms, embeddings, tools, MCP servers) so admins see what
  a secret powers before deleting; `dependent: :nullify` protects data.
- **Gap in Sloth AI**: keys are site settings pushed to bridge files. Fine for
  2 keys, but no `used_by`/audit, and clearing the site setting does not clear
  the bridge file (stale key keeps working).

### 2.3 Agent framework (`ai_agents` + `Bot` + `ToolRunner`)
- Agents have: `system_prompt` (10MB!), `allowed_group_ids`, `tools` (json),
  `temperature/top_p`, `rag_chunk_tokens`, `default_llm_id`, `force_default_llm`,
  `vision_enabled`, `response_format`, `examples`, `show_thinking`.
- `DiscourseAi::Agents::Bot` implements a real **tool loop with budgets**:
  `DEFAULT_MAX_TURN_TOKENS = 32_000`, tool invocation budget, context
  compression, final-answer hints when budget exhausted.
- `ToolRunner` sandboxes tools (timeout 2000ms, memory cap, max HTTP requests,
  max sleep) — security hardened.
- **Gap in Sloth AI**: smolagents CodeAgent is a black box subprocess with
  `max_steps=3`; no token budget, no per-user limits, no tool sandbox tuning,
  no custom tools. For the forum bot use-case this is acceptable, but agents
  with budgets + group permissions are strictly more capable.

### 2.4 Tools + MCP (`ai_tools`, `ai_mcp_servers`, `ai_tool_secret_bindings`)
- Tool registry with per-tool **test** endpoint, import/export, MCP server
  support, secret bindings per tool.
- **Gap in Sloth AI**: only one hardcoded tool (web_search). No way for admins
  to add e.g. a "read topic", "search users", "RAG over forum" tool.

### 2.5 Usage & quotas (`ai_api_request_stats`, `llm_quotas`, `ai_usage`)
- Per-request token/cost stats → usage report (30-day, filter by feature/model).
- Per-model costs (`input_cost/output_cost`) enable $ tracking.
- `llm_quotas`: per-group limits (max_tokens / max_usages / max_cost over a
  duration) — **enforcement**, not just display.
- **Gap in Sloth AI**: we display provider quota % but track NO actual token
  spend and have no per-user/group limits. Sloth AI has a simple
  `gemini_daily_limit_per_user` counter (PluginStore) but no cost or tokens.

### 2.6 Provider endpoint abstraction (`Completions::Endpoints`)
- `Llm` facade: `dialect` (prompt translation) + `endpoint` (HTTP gateway).
- `provider_names` registry; `provider_capabilities` for the admin form.
- **Gap in Sloth AI**: bridge backends are Python classes; the Ruby side has
  no abstraction — routing is prefix-based in `backend_for`.

## 3. Things Sloth AI has that Discourse AI does NOT

| Feature | Sloth AI | Discourse AI |
|---|---|---|
| Antigravity (Google AI Pro) OAuth backend | ✅ full backend + re-auth UI | ❌ no (Google API key / Vertex only) |
| Provider subscription quota monitor (remaining % + resets) | ✅ live grouped pools | ❌ only self-imposed quotas |
| OpenCode Go / Together AI chat-bot routing via bridge | ✅ | ❌ (would need manual OpenAI-endpoint config per model) |
| LDR (local deep research) pipeline integration | ✅ | ❌ (Discourse AI has no deep-research report pipeline) |
| Bot auto-provisioning (`ai_<model>` users) with mention-rewrite on rename | ✅ | ❌ (bots are configured agents, users created manually) |
| Grouped quota display (models sharing a token pool) | ✅ | ❌ |

## 4. Honest assessment

**Discourse AI is the better *platform***: registry-driven, provider-agnostic,
cost-aware, sandboxed tools, per-group quotas, big agent framework. It is the
correct long-term home if we want multiple providers/tools/cost controls and
are willing to give up Antigravity-subscription quota features.

**Sloth AI is the better *bridge to our infra***: it owns the Antigravity OAuth
subscription (the thing the forum actually pays for), OpenCode Go + Together
routing with SearXNG grounding, the LDR pipeline, and the AI Pro re-auth UI.
Discourse AI's LLMs currently point at our bridge (`172.17.0.1:8787`) as an
`open_ai` endpoint — i.e. **Discourse AI already consumes Sloth AI as its
provider** for Gemini models.

## 5. Recommended direction

1. **Keep Sloth AI as the provider layer** (Antigravity/OAuth/OpenCode/Together
   + SearXNG + LDR). Do NOT duplicate Discourse AI's agent framework.
2. **Adopt Discourse AI's ideas into Sloth AI's admin page** (cheap wins):
   - Model registry as a JSON site setting (id, display, provider, url) instead
     of hardcoded prefixes → add models without code changes.
   - Secrets `used_by`-style display (show which bots use each key).
   - A per-bot/per-user **usage counter with token estimates** (we already have
     the daily counter; add cost column from usage payloads).
3. **Optionally integrate**: let Discourse AI agents use Sloth AI models via the
   bridge (already works), and let Sloth AI bots opt into Discourse AI's
   agent budgets/tools for advanced use-cases.

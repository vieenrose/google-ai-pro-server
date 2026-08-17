# frozen_string_literal: true

DiscourseGemini::Engine.routes.draw do
  # Public quota/status page.
  get "/sloth-ai" => "quota#index"
  # Legacy alias — old /quota links redirect to /sloth-ai.
  get "/quota" => redirect("/sloth-ai")
  # Admin panel link (Ember route redirects here via /full).
  get "/admin/plugins/sloth-ai" => "quota#index"
  get "/admin/plugins/sloth-ai/full" => "quota#index"
  # Save plugin settings (OpenCode key / bridge token / bridge URL).
  post "/admin/plugins/sloth-ai/settings" => "quota#update_settings"
  # Google AI Pro re-auth flow.
  post "/admin/plugins/sloth-ai/reauth" => "quota#reauth_url"
  post "/admin/plugins/sloth-ai/reauth/exchange" => "quota#reauth_exchange"
  # Sync model registry + secrets from Discourse AI to the bridge.
  post "/admin/plugins/sloth-ai/sync-providers" => "quota#sync_providers"
  # Create/rename bot users for the checked models.
  post "/admin/plugins/sloth-ai/bots" => "quota#create_bots"
end

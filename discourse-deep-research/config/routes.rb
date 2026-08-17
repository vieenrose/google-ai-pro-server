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
  # Create/rename bot users for the checked models.
  post "/admin/plugins/sloth-ai/bots" => "quota#create_bots"
end

# frozen_string_literal: true

DiscourseGemini::Engine.routes.draw do
  # Public quota/status page.
  get "/sloth-ai" => "quota#index"
  # Legacy alias — old /quota links redirect to /sloth-ai.
  get "/quota" => redirect("/sloth-ai")
  # Admin panel link (Ember route redirects here via /full).
  get "/admin/plugins/sloth-ai" => "quota#index"
  get "/admin/plugins/sloth-ai/full" => "quota#index"
  # Google AI Pro re-auth flow.
  post "/admin/plugins/sloth-ai/reauth" => "quota#reauth_url"
  post "/admin/plugins/sloth-ai/reauth/exchange" => "quota#reauth_exchange"
end

# frozen_string_literal: true

DiscourseGemini::Engine.routes.draw do
  # Public quota monitor — visible to all users.
  get "/quota" => "quota#index"
  # Admin panel link (Ember route redirects here via /full).
  get "/admin/plugins/sloth-ai" => "quota#index"
  get "/admin/plugins/sloth-ai/full" => "quota#index"
  # Save plugin settings (OpenCode key / bridge token / bridge URL).
  post "/admin/plugins/sloth-ai/settings" => "quota#update_settings"
end

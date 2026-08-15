# frozen_string_literal: true

DiscourseGemini::Engine.routes.draw do
  # Public quota monitor — visible to all users.
  get "/quota" => "quota#index"
  # Admin panel link (Ember route redirects here via /full).
  get "/admin/plugins/antigravity-quota" => "quota#index"
  get "/admin/plugins/antigravity-quota/full" => "quota#index"
end

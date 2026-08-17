# name: Sloth AI Plugin
# about: Sloth AI — Google AI Pro (Antigravity) bridge, quota monitor, subscription management
# version: 0.4.0
# authors: Luigi LIU
# url: https://github.com/vieenrose/google-ai-pro-server

enabled_site_setting :gemini_enabled

register_asset "stylesheets/gemini.scss"

load File.expand_path("../lib/deep_research_bridge.rb", __FILE__)
require_relative "lib/discourse_gemini"

add_admin_route "discourse_gemini.quota.title", "sloth-ai"

after_initialize do
  Discourse::Application.routes.append do
    mount DiscourseGemini::Engine, at: "/"
  end
end

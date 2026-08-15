# frozen_string_literal: true

module DiscourseGemini
  # Rails engine so plugin controllers/views autoload reliably and routes
  # mount like the chat plugin's engine. Routes live in config/routes.rb.
  class Engine < ::Rails::Engine
    engine_name "discourse_gemini"
    isolate_namespace DiscourseGemini
  end
end

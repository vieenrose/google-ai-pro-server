# frozen_string_literal: true

module Jobs
  # Auto-sync the Discourse AI model registry (llm_models + ai_secrets) to the
  # Sloth AI bridge every 5 minutes. Real-time changes also trigger an
  # immediate sync via after_commit hooks (see plugin.rb); this scheduled job
  # is the safety net (e.g. after bridge restarts or missed events).
  class SyncSlothProviders < ::Jobs::Scheduled
    every 5.minutes

    def execute(args)
      ::DiscourseGemini.sync_providers_from_discourse_ai
    rescue StandardError => e
      Rails.logger.warn("[discourse-deep-research] scheduled providers sync failed: #{e.message}")
    end
  end
end

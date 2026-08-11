# frozen_string_literal: true

module ::Jobs
  # Runs Gemini Deep Research (/deep topic) in the background and posts the
  # cited report when it finishes (1–5 minutes).
  class GeminiDeepResearch < ::Jobs::Base
    sidekiq_options retry: 1

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      # let the user know it started
      DiscourseGemini.post_as_bot(
        topic_id: post.topic_id,
        raw: I18n.t("discourse_gemini.deep_started"),
      )

      result = GeminiBridge.new.deep_research(
        args[:topic],
        model: SiteSetting.gemini_model,
      )

      if result["error"].present?
        raw = I18n.t("discourse_gemini.error", error: result["error"].to_s[0, 500])
      else
        sources = (result["sources"] || []).map.with_index(1) { |u, i| "[#{i}] #{u}" }.join("\n")
        raw = <<~MD
          # 📚 Gemini Deep Research: #{args[:topic]}

          #{result['report']}

          ---
          **Sources (#{(result['sources'] || []).size})**

          #{sources}
        MD
      end

      DiscourseGemini.post_as_bot(topic_id: post.topic_id, raw: raw)
    end
  end
end

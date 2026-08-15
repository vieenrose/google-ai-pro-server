# frozen_string_literal: true

module ::Jobs
  # Runs the SELF-HOSTED Local Deep Research pipeline (local_deep_research
  # package, SearXNG + deepseek-v4-flash) and posts the cited report.
  class GeminiLocalDeepResearch < ::Jobs::Base
    sidekiq_options retry: 1, queue: "low"

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      DiscourseGemini.post_as_bot(
        topic_id: post.topic_id,
        reply_to_post_number: post.reply_to_post_number,
        raw: I18n.t("discourse_gemini.local_deep_started"),
        username: "local-deep-research",
      )

      result = GeminiBridge.new.local_deep_research(args[:topic])

      if result["error"].present?
        raw = I18n.t("discourse_gemini.error", error: result["error"].to_s[0, 500])
      else
        sources = (result["sources"] || []).map.with_index(1) { |u, i| "[#{i}] #{u}" }.join("\n")
        raw = <<~MD
          # 🔬 Local Deep Research: #{args[:topic]}

          #{result["report"]}

          ---
          **Sources (#{(result["sources"] || []).size})**

          #{sources}
        MD
      end

      DiscourseGemini.post_as_bot(
        topic_id: post.topic_id,
        reply_to_post_number: post.reply_to_post_number,
        raw: raw,
        username: "local-deep-research",
      )
    end
  end
end

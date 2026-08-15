# frozen_string_literal: true

module ::Jobs
  # Runs the SELF-HOSTED Local Deep Research pipeline (local_deep_research
  # package, SearXNG + deepseek-v4-flash) and posts the cited report.
  class GeminiLocalDeepResearch < ::Jobs::Base
    sidekiq_options retry: 0, queue: "low"

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      # Deduplicate: one report per triggering post (sidekiq retries and
      # duplicate mentions must not spawn multiple LDR runs).
      return if post.custom_fields["ldr_report_posted"].present?

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
        sources =
          (result["sources"] || []).map.with_index(1) do |s, i|
            if s.is_a?(Hash)
              title = s["title"].presence || s["snippet"].to_s.truncate(80).presence || "source"
              url = s["url"].presence || s["link"].presence || s["id"].presence
              url.present? ? "[#{i}] #{title} — #{url}" : "[#{i}] #{title}"
            else
              "[#{i}] #{s}"
            end
          end.join("\n")
        raw = <<~MD
          # 🔬 Local Deep Research: #{args[:topic]}

          #{result["report"]}

          ---
          **Sources (#{(result["sources"] || []).size})**

          #{sources}
        MD
      end

      bot_post =
        DiscourseGemini.post_as_bot(
          topic_id: post.topic_id,
          reply_to_post_number: post.reply_to_post_number,
          raw: raw,
          username: "local-deep-research",
        )
      if bot_post.present?
        post.custom_fields["ldr_report_posted"] = true
        post.save_custom_fields
      end
    end
  end
end

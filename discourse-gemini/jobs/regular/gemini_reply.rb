# frozen_string_literal: true

module ::Jobs
  # Posts a Gemini chat reply to the topic that summoned it (@gemini …).
  class GeminiReply < ::Jobs::Base
    sidekiq_options retry: 2

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      result = GeminiBridge.new.chat(
        [{ role: "user", content: args[:prompt] }],
        model: SiteSetting.gemini_model,
      )

      raw =
        if result["error"].present?
          I18n.t("discourse_gemini.error", error: result["error"].to_s[0, 500])
        else
          "#{I18n.t('discourse_gemini.chat_prefix')}\n\n#{result['reply']}"
        end

      DiscourseGemini.post_as_bot(topic_id: post.topic_id, raw: raw)
    end
  end
end

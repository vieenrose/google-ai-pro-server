# frozen_string_literal: true

module ::Jobs
  # Posts a Gemini chat reply to the topic that summoned it (@gemini …).
  class GeminiReply < ::Jobs::Base
    sidekiq_options retry: 2

    # Include the recent discussion of the topic as context so Gemini can
    # answer with awareness of the conversation (stateless by default).
    # Conversation memory: include as much of the thread's history as fits a
    # token budget (Gemini's context is large, so prefer breadth over truncation).
    CONTEXT_CHAR_BUDGET = 60_000
    MAX_POSTS = 200
    MAX_POST_CHARS = 1_500

    def build_context(post)
      return "" if post.topic.blank?

      bot_usernames = [SiteSetting.gemini_bot_username, "deep-research"].map(&:to_s)
      posts =
        post
          .topic
          .posts
          .where("post_number < ?", post.post_number)
          .order(:post_number)
          .last(MAX_POSTS)

      # walk newest -> oldest, keep as many as fit the budget, then reverse
      kept = []
      budget = CONTEXT_CHAR_BUDGET
      posts.reverse_each do |p|
        raw = p.raw.to_s.gsub(/@(gemini|deep-research)\b/i, "").strip
        next if raw.blank? || bot_usernames.include?(p.user&.username)
        line = "\u2022 #{p.user&.username}: #{raw[0, MAX_POST_CHARS]}"
        break if line.length > budget
        kept << line
        budget -= line.length
      end
      kept.reverse!

      return "" if kept.empty?

      "Topic: \"#{post.topic.title}\"\nConversation history in this topic (oldest first):\n#{kept.join("\n")}"
    end

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      context = build_context(post)
      messages = []
      messages << { role: "system", content: context } if context.present?
      messages << { role: "user", content: args[:prompt] }

      result = GeminiBridge.new.chat(
        messages,
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

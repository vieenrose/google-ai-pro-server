# frozen_string_literal: true

module ::Jobs
  # Built-in AI chat bot reply (one-plugin mode).
  # Streams the bridge's OpenAI-compatible /v1/chat/completions into a bot
  # post. Image generation is handled by the bridge: it detects image
  # requests, switches to the image model, uploads the result to the forum
  # and appends the markdown to the stream.
  class GeminiChatReply < ::Jobs::Base
    sidekiq_options retry: 1, queue: "default"

    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?

      bot = User.find_by(username: args[:bot_username])
      return if bot.blank?

      model = DiscourseGemini.bot_model(args[:bot_username])
      return if model.blank?

      messages = DiscourseGemini.build_chat_messages(post, bot)

      bot_post =
        PostCreator.new(
          bot,
          topic_id: post.topic_id,
          reply_to_post_number: post.post_number,
          raw: I18n.t("discourse_gemini.chat_thinking"),
          skip_validations: true,
          guardian: Guardian.new(bot),
        ).create!
      return if bot_post.blank?

      buffer = +""
      last_flush = Time.now

      begin
        GeminiBridge.new.stream_chat_completions(messages, model: model) do |delta|
          buffer << delta
          next unless Time.now - last_flush > 1.5

          PostRevisor.new(bot_post).revise!(bot, raw: buffer, skip_validations: true)
          last_flush = Time.now
        end
      rescue StandardError => e
        buffer << "\n\n⚠️ #{I18n.t("discourse_gemini.error", error: e.message.to_s[0, 300])}"
      ensure
        raw = buffer.presence || I18n.t("discourse_gemini.chat_empty")
        PostRevisor.new(bot_post).revise!(bot, raw: raw, skip_validations: true)
        # Force a final rebake: mid-stream flushes may have left the post's
        # cooked from a partial buffer (unbalanced ** markers etc.), so the
        # stored cooked can silently disagree with the final raw. A rebake
        # guarantees the rendered output always matches the final text.
        begin
          bot_post.rebake!
        rescue StandardError
          nil
        end
      end
    end
  end
end

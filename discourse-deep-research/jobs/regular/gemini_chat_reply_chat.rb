# frozen_string_literal: true

module ::Jobs
  # Built-in AI chat bot reply for Discourse CHAT (one-plugin mode).
  # Streams the bridge's /v1/chat/completions into a chat message in the
  # same channel (and thread). Image requests are handled by the bridge,
  # which uploads generated images to the forum and appends the markdown.
  class GeminiChatReplyChat < ::Jobs::Base
    sidekiq_options retry: 1, queue: "default"

    def execute(args)
      message = ::Chat::Message.find_by(id: args[:message_id])
      return if message.blank?

      bot = User.find_by(username: args[:bot_username])
      return if bot.blank?

      model = DiscourseGemini.bot_model(args[:bot_username])
      return if model.blank?

      messages = DiscourseGemini.build_chat_channel_messages(message, bot)
      guardian = Guardian.new(bot)

      ChatSDK::Message.create_with_stream(
        raw: I18n.t("discourse_gemini.chat_thinking"),
        channel_id: message.chat_channel_id,
        thread_id: message.thread_id,
        guardian: guardian,
      ) do |helper, _chat_message|
        begin
          GeminiBridge.new.stream_chat_completions(messages, model: model) do |delta|
            helper.stream(raw: delta)
          end
        rescue StandardError => e
          helper.stream(raw: "\n\n⚠️ #{I18n.t("discourse_gemini.error", error: e.message.to_s[0, 300])}")
        end
      end
    end
  end
end

# frozen_string_literal: true

module ::Jobs
  # Posts a short system notice (permission / rate-limit) to the topic.
  class GeminiNotice < ::Jobs::Base
    def execute(args)
      post = Post.find_by(id: args[:post_id])
      return if post.blank?
      DiscourseGemini.post_as_bot(topic_id: post.topic_id, raw: args[:text].to_s)
    end
  end
end

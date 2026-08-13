# Plugin: Google Deep Research for Discourse
# Lets forum users trigger Deep Research in a topic (@deep-research … or
# /deep …). The research report (sub-questions → verification → sourced
# report) is produced by the local bridge (bridge/server.py), which consumes
# your Google AI Pro subscription. No Gemini API key required by the plugin
# itself; the bridge holds the model configuration.
#
# NOTE: internal identifiers (setting keys gemini_*, module DiscourseGemini,
# job names, the discourse-gemini-bot-post CSS class) were intentionally kept
# from the former "discourse-gemini" plugin so existing forum data and stored
# settings survive the rename untouched.

# name: discourse-deep-research
# about: Deep Research for Discourse (@deep-research / /deep) powered by your Google AI Pro subscription
# version: 0.2.0
# authors: Luigi LIU
# url: https://github.com/vieenrose/google-ai-pro-server

enabled_site_setting :gemini_enabled

register_asset "stylesheets/gemini.scss"

load File.expand_path("../lib/deep_research_bridge.rb", __FILE__)

# jobs auto-load via Discourse plugin job autoloading

after_initialize do
  # load jobs now that Jobs::Base exists
  Dir["#{__dir__}/jobs/**/*.rb"].each { |f| load f }
  # ── bot user ──────────────────────────────────────────────────────────────
  # We avoid relying on the `users.bot` column (not present in all Discourse
  # versions) and instead mark the bot with a custom field + username check.
  BOT_FIELD = "discourse_gemini_bot"

  def mark_as_bot(user)
    user.custom_fields[BOT_FIELD] = true
    user.save_custom_fields
    # newer Discourse exposes a real `bot` column — set it when available
    if User.column_names.include?("bot")
      user.update_columns(bot: true)
    end
  rescue StandardError
    # custom_fields may not be saved for system user — that's fine
  end

  def deep_research_bot?(user)
    return false if user.blank?
    user.id == Discourse::SYSTEM_USER_ID ||
      user.username == "deep-research" ||
      user.custom_fields[BOT_FIELD] == true
  end

  # ── permission + rate-limit helpers ───────────────────────────────────────
  module ::DiscourseGemini
    def self.allowed_for?(user)
      return false if user.blank?
      return false if deep_research_bot?(user)
      groups = SiteSetting.gemini_allowed_groups
      return true if groups.blank?
      group_ids = Group.where(name: groups.split("|")).pluck(:id)
      return false if group_ids.empty?
      GroupUser.where(group_id: group_ids, user_id: user.id).exists?
    end

    def self.remaining_uses(user)
      max = SiteSetting.gemini_daily_limit_per_user.to_i
      key = "gemini_usage:#{user.id}:#{Date.today}"
      used = PluginStore.get("discourse_gemini", key).to_i
      [max - used, 0].max
    end

    def self.record_use(user, count = 1)
      key = "gemini_usage:#{user.id}:#{Date.today}"
      used = PluginStore.get("discourse_gemini", key).to_i
      PluginStore.set("discourse_gemini", key, used + count)
    end

    def self.post_as_bot(topic_id:, raw:, title: nil, username: "deep-research")
      bot = User.find_by(username: username) || Discourse.system_user
      cooked = "<div class=\"discourse-gemini-bot-post\">\n\n#{raw}\n\n</div>"
      PostCreator.new(
        bot,
        topic_id: topic_id,
        raw: cooked,
        skip_validations: true,
        guardian: Guardian.new(bot),
      ).create!
    end

    def self.run_job(user, post, payload)
      if !allowed_for?(user)
        Jobs.enqueue(:gemini_notice, post_id: post.id,
                     text: I18n.t("discourse_gemini.not_allowed"))
        return
      end
      if remaining_uses(user) < 1
        Jobs.enqueue(:gemini_notice, post_id: post.id,
                     text: I18n.t("discourse_gemini.rate_limited"))
        return
      end
      record_use(user, 1)
      Jobs.enqueue(:gemini_deep_research, payload.merge(post_id: post.id))
    end

    def self.ensure_deep_research_bot!
      username = "deep-research"
      user = User.find_by(username: username)
      return user if user
      begin
        User.transaction do
          user = User.create!(
            username: username,
            name: "Deep Research",
            email: "#{username}@discourse-gemini.invalid",
            active: true,
            approved: true,
            trust_level: TrustLevel.levels[:leader],
          )
          user.activate
        end
        user.custom_fields[BOT_FIELD] = true
        user.save_custom_fields
        if User.column_names.include?("bot")
          user.update_columns(bot: true)
        end
      rescue ActiveRecord::RecordInvalid, PG::UniqueViolation
        user = User.find_by(username: username)
      end
      user
    end
  end

  ::DiscourseGemini.ensure_deep_research_bot!

  # ── trigger: detect @deep-research / /deep in new posts ───────────────────
  on(:post_created) do |post, _opts, user|
    next unless SiteSetting.gemini_enabled
    next if user.blank? || deep_research_bot?(user)
    raw = post.raw.to_s

    if raw =~ /\A@deep-research[:\s]+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_job(user, post, { topic: $1.strip })
    elsif raw =~ /\A\/deep\s+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_job(user, post, { topic: $1.strip })
    end
  end
end

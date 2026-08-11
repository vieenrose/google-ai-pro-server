# Plugin: Discourse Gemini (AI Pro)
# Lets forum users summon Gemini into a topic (@gemini …) or trigger Deep
# Research (/deep …). Backed by the local bridge (bridge/server.py), which
# consumes your Google AI Pro subscription via the Antigravity CLI — no Gemini
# API key.

# name: discourse-gemini
# about: Summon Gemini (@gemini) and run Deep Research (/deep) using your Google AI Pro subscription
# version: 0.1.0
# authors: Luigi LIU
# url: https://github.com/vieenrose/google-ai-pro-server

enabled_site_setting :gemini_enabled

register_asset "stylesheets/gemini.scss"

load File.expand_path("../lib/gemini_bridge.rb", __FILE__)

# load jobs explicitly (works across Discourse versions)
Dir["#{__dir__}/jobs/**/*.rb"].each { |f| load f }

after_initialize do
  # ── bot user ──────────────────────────────────────────────────────────────
  def create_gemini_bot!
    username = SiteSetting.gemini_bot_username.presence || "gemini"
    user = User.find_by(username: username)
    return user if user
    begin
      User.transaction do
        user = User.create!(
          username: username,
          name: "Gemini",
          email: "#{username}@discourse-gemini.invalid",
          active: true,
          approved: true,
          trust_level: TrustLevel.levels[:leader],
          bot: true,
        )
        user.activate
      end
    rescue ActiveRecord::RecordInvalid, PG::UniqueViolation
      user = User.find_by(username: username) || Discourse.system_user
    end
    user
  end

  gemini_bot = create_gemini_bot!
  if gemini_bot
    DiscourseEvent.on(:user_created) do |user|
      next unless user.username == SiteSetting.gemini_bot_username
      user.update!(bot: true) unless user.bot
    end
  end

  # ── permission + rate-limit helpers ───────────────────────────────────────
  module ::DiscourseGemini
    def self.allowed_for?(user)
      return false if user.blank? || user.bot?
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

    def self.post_as_bot(topic_id:, raw:, title: nil)
      bot = User.find_by(username: SiteSetting.gemini_bot_username) || Discourse.system_user
      cooked = "<div class=\"discourse-gemini-bot-post\">\n\n#{raw}\n\n</div>"
      PostCreator.new(
        bot,
        topic_id: topic_id,
        raw: cooked,
        skip_validations: true,
        guardian: Guardian.new(bot),
      ).create!
    end

    def self.run_job(user, post, kind, payload)
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
      args = payload.merge(post_id: post.id, kind: kind)
      Jobs.enqueue(kind == "deep" ? :gemini_deep_research : :gemini_reply, args)
    end
  end

  # ── trigger: detect @gemini / /deep in new posts ──────────────────────────
  on(:post_created) do |post, _opts, user|
    next unless SiteSetting.gemini_enabled
    next if post.user&.bot? || user.blank?
    raw = post.raw

    if raw =~ /\A\/deep\s+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_job(user, post, "deep", { topic: $1.strip })
    elsif raw =~ /\A@gemini[:\s]+(.+)\z/m
      DiscourseGemini.run_job(user, post, "chat", { prompt: $1.strip })
    end
  end
end

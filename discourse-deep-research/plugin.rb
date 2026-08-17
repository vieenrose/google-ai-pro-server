# Plugin: Google Deep Research for Discourse
# Lets forum users trigger Deep Research in a topic (@deep-research … or
# /deep …). The research report (sub-questions → verification → sourced
# report) is produced by the local bridge (bridge/server.py), which consumes
# your Google AI Pro subscription. No Gemini API key required by the plugin
# itself; the bridge holds the model configuration.
# ---
# NOTE: internal identifiers (setting keys gemini_*, module DiscourseGemini,
# job names, the discourse-gemini-bot-post CSS class) were intentionally kept
# from the former "discourse-gemini" plugin so existing forum data and stored
# settings survive the rename untouched.

# name: Sloth AI Plugin
# about: Sloth AI — forum AI bots (Google AI Pro via Antigravity + OpenCode Go deepseek/mimo), quota monitor, deep research
# version: 0.3.0
# authors: Luigi LIU
# url: https://github.com/vieenrose/google-ai-pro-server

enabled_site_setting :gemini_enabled

register_asset "stylesheets/gemini.scss"

load File.expand_path("../lib/deep_research_bridge.rb", __FILE__)
require_relative "lib/discourse_gemini"

add_admin_route "discourse_gemini.quota.title", "sloth-ai"

# jobs auto-load via Discourse plugin job autoloading

after_initialize do
  Discourse::Application.routes.append do
    mount DiscourseGemini::Engine, at: "/"
  end

  # Hide all Sloth AI plugin settings from the native Settings page
  # (admin/site_settings/category/discourse_gemini would otherwise auto-render
  # them). The plugin's own admin page (/admin/plugins/sloth-ai) is the single
  # place to manage them. Settings still work via SiteSetting.* accessors and
  # keep their defaults from settings.yml.
  register_modifier(:hidden_site_settings) do |hidden|
    hidden + %i[
      gemini_enabled gemini_bridge_url gemini_bridge_token gemini_opencode_api_key
      gemini_allowed_groups gemini_bot_username gemini_model
      gemini_daily_limit_per_user gemini_bot_models gemini_chat_enabled
      gemini_chat_history_posts gemini_deep_research_enabled
      gemini_deep_research_max_questions
    ]
  end

  # When an admin changes the OpenCode Go API key site setting, push it to
  # the bridge immediately (the bridge persists it over its env var).
  on(:site_setting_changed) do |name, _old_val, current|
    next unless name == :gemini_opencode_api_key
    key = current.to_s
    if key.present?
      begin
        GeminiBridge.new.push_opencode_key(key)
      rescue StandardError => e
        Rails.logger.warn("[discourse-deep-research] push_opencode_key failed: #{e.message}")
      end
    end
  end

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
    def self.deep_research_bot?(user)
      return false if user.blank?
      user.id == Discourse::SYSTEM_USER_ID ||
        user.username == "deep-research" ||
        user.custom_fields[BOT_FIELD] == true
    end

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


    # ── built-in chat bots (one-plugin mode) ───────────────────────────────
    def self.bot_config
      raw = SiteSetting.gemini_bot_models.presence || "{}"
      JSON.parse(raw)
    rescue JSON::ParserError
      {}
    end

    def self.bot_model(username)
      bot_config[username].presence
    end

    def self.chat_bot?(user)
      return false if user.blank?
      bot_config.key?(user.username)
    end

    # Bot username for a native model id: ai_ + model name, e.g.
    # "gemini-3.6-flash" → "ai_gemini-3.6-flash".
    #
    # Usernames are capped at SiteSetting.max_username_length (20), so for
    # over-long model ids we drop the tier suffix: "gemini-3.7-flash-tiered"
    # → "ai_gemini-3.7-flash" (the config still maps to the real model id).
    # If a model is already enabled under a shorter legacy name (e.g.
    # ai_gemini_image for gemini-3.1-flash-image), that name is reused.
    def self.bot_username_for(model_id)
      name = "ai_#{model_id}"
      return name if valid_bot_username?(name)

      # reuse an existing config mapping (legacy short names)
      existing = bot_config.key(model_id)
      return existing if existing && valid_bot_username?(existing)

      short = model_id.dup
      # drop trailing tier markers until the name fits
      short = short.sub(/-(tiered|thinking|preview|image)\z/, "") while short =~ /-(tiered|thinking|preview|image)\z/
      alias_name = "ai_#{short}"
      return alias_name if valid_bot_username?(alias_name)

      # last resort: truncate to the max length
      "ai_#{model_id}"[0, SiteSetting.max_username_length]
    end

    # Pretty display name for a raw model id, e.g.
    #   "deepseek-v4-flash"        → "DeepSeek V4 Flash"
    #   "minimax-m3"              → "MiniMax M3"
    #   "glm-5.3"                 → "GLM 5.3"
    #   "gemini-3.6-flash-high"   → "Gemini 3.6 Flash (High)"
    #   "qwen3.8-max"             → "Qwen3.8 Max"
    #
    # Names that are already humanized (spaces, mixed case, parentheses — e.g.
    # the Antigravity quota displayName "Gemini 3.6 Flash (High)") are returned
    # unchanged; only raw lowercase ids get normalized.
    def self.normalize_model_name(raw)
      s = raw.to_s.strip
      return s if s.empty?

      # already humanized (contains a space or an uppercase letter)
      return s if s.include?(" ") || s =~ /[A-Z]/

      # uppercase known vendor prefixes
      vendor_map = {
        "gemini" => "Gemini", "deepseek" => "DeepSeek", "mimo" => "Mimo",
        "claude" => "Claude", "minimax" => "MiniMax", "kimi" => "Kimi",
        "glm" => "GLM", "qwen" => "Qwen", "grok" => "Grok",
        "gpt" => "GPT", "hy3" => "Hy3", "tab" => "Tab",
        "gpt-oss" => "GPT-OSS",
      }
      parts = s.split("-")
      first = parts.shift.to_s
      vendor = vendor_map[first] || vendor_map["#{first}-#{parts[0]}"] || first.capitalize
      parts.shift if vendor_map["#{first}-#{parts[0]}"] # consumed second part as vendor

      # tier suffix → parenthesized label
      tier = {
        "high" => "(High)", "low" => "(Low)", "medium" => "(Medium)",
        "thinking" => "(Thinking)", "tiered" => "(Tiered)",
        "lite" => "(Lite)", "preview" => "(Preview)",
        "omni" => "(Omni)", "code" => "(Code)",
      }
      # bare model-tier words (no parens): pro, flash, max, plus, luna …
      bare = { "pro" => "Pro", "flash" => "Flash", "max" => "Max",
               "plus" => "Plus", "luna" => "Luna", "min" => "Min" }
      rest = []
      numeric_run = []
      flush_run = lambda do
        unless numeric_run.empty?
          rest << numeric_run.join(".")  # "4","6" → "4.6"
          numeric_run = []
        end
      end
      parts.each do |p|
        if p =~ /\A\d/
          numeric_run << p
        else
          flush_run.call
          if tier.key?(p.downcase)
            rest << tier[p.downcase]
          elsif p.downcase == "extra" && parts[parts.index(p) + 1]&.downcase == "low"
            rest << "(Extra Low)"
            parts[parts.index(p) + 1] = "__skip__"
          elsif bare.key?(p.downcase)
            rest << bare[p.downcase]
          elsif p =~ /\Av\d/i
            rest << p.sub(/\Av/i, "V").upcase
          else
            rest << p.capitalize unless p == "__skip__"
          end
        end
      end
      flush_run.call
      name = ([vendor] + rest).join(" ")
      # normalise paren spacing: "Flash (High)", never "(X)(Y)"
      name = name.gsub(/ {2,}/, " ").gsub(/\(([^)]*)\) \(/) { "(#{$1}) (" }.strip
    end


    def self.valid_bot_username?(username)
      UsernameValidator.new(username).valid_format?
    end

    # Find the existing bot user mapped to this model (any naming) so we can
    # rename it to the current ai_<model> convention.
    def self.bot_user_for_model(model_id)
      config = bot_config
      name = config.key(model_id) # old naming, e.g. ai_gemini_3_6_flash
      return User.find_by(username: name) if name
      User.find_by(username: bot_username_for(model_id))
    end

    def self.create_bot_user!(username)
      user = nil
      User.transaction do
        user =
          User.create!(
            username: username,
            name: username.titleize,
            email: "#{username}@discourse-gemini.invalid",
            active: true,
            approved: true,
            trust_level: TrustLevel.levels[:leader],
          )
        user.activate
      end
      user
    rescue ActiveRecord::RecordInvalid, PG::UniqueViolation
      # raced with another boot — fine
      User.find_by(username: username)
    end

    def self.ensure_chat_bots!
      bot_config.each_key do |username|
        next if User.find_by(username: username).present?
        create_bot_user!(username)
      end
    end

    # Build OpenAI-style message history for a chat bot reply.
    def self.build_chat_messages(post, bot)
      limit = SiteSetting.gemini_chat_history_posts.to_i.clamp(1, 30)
      topic_posts =
        post
          .topic
          .posts
          .where("post_number <= ?", post.post_number)
          .where.not(post_type: Post.types[:small_action])
          .order("post_number DESC")
          .limit(limit)
          .to_a
          .reverse
      messages = []
      topic_posts.each do |p|
        role = p.user_id == bot.id || (p.user_id.to_i < 0 && chat_bot_username?(p.user)) ? "assistant" : "user"
        content = p.cooked.present? ? PrettyText.excerpt(p.cooked, 3000, strip_links: true) : p.raw.to_s[0, 3000]
        content = content.to_s.gsub(/<[^>]+>/, " ")
        author = p.user&.username || "user"
        prefix = role == "user" ? "#{author}: " : ""
        messages << { role: role, content: "#{prefix}#{content}" }
      end
      # keep the last message as the triggering user turn
      messages
    end

    def self.chat_bot_username?(user)
      user.respond_to?(:username) && bot_config.key?(user.username)
    end

    def self.post_as_bot(topic_id:, raw:, title: nil, username: "deep-research", reply_to_post_number: nil)
      bot = User.find_by(username: username) || Discourse.system_user
      cooked = "<div class=\"discourse-gemini-bot-post\">\n\n#{raw}\n\n</div>"
      PostCreator.new(
        bot,
        topic_id: topic_id,
        reply_to_post_number: reply_to_post_number,
        raw: cooked,
        skip_validations: true,
        guardian: Guardian.new(bot),
      ).create!
    end

    def self.run_local_job(user, post, payload)
      if !allowed_for?(user)
        Jobs.enqueue(:gemini_notice, post_id: post.id, text: I18n.t("discourse_gemini.not_allowed"))
        return
      end
      if remaining_uses(user) < 1
        Jobs.enqueue(:gemini_notice, post_id: post.id, text: I18n.t("discourse_gemini.rate_limited"))
        return
      end
      record_use(user, 1)
      Jobs.enqueue(:gemini_local_deep_research, payload.merge(post_id: post.id))
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

    def self.ensure_local_deep_research_bot!
      username = "local-deep-research"
      user = User.find_by(username: username)
      return user if user
      begin
        User.transaction do
          user = User.create!(
            username: username,
            name: "Local Deep Research",
            email: "#{username}@discourse-gemini.invalid",
            active: true,
            approved: true,
            trust_level: TrustLevel.levels[:leader],
          )
          user.activate
        end
        user.custom_fields[BOT_FIELD] = true
        user.save_custom_fields
      rescue ActiveRecord::RecordInvalid, PG::UniqueViolation
        user = User.find_by(username: username)
      end
      user
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
  ::DiscourseGemini.ensure_local_deep_research_bot!
  ::DiscourseGemini.ensure_chat_bots!

  # ── chat rendering fix ───────────────────────────────────────────────────
  # Chat's markdown does not render <details> HTML, so the folded ai-thinking
  # block leaks as raw text in chat messages. Strip it from the cooked output
  # of bot chat messages (topics render it folded; chat shows the clean
  # answer only). The raw message keeps the thinking for reference.
  on(:chat_message_processed) do |doc, message|
    next if message.user_id.to_i >= 0
    paragraphs = doc.css("p")
    i = 0
    while i < paragraphs.length
      text = paragraphs[i].text.to_s
      if text.include?("<details class='ai-thinking'")
        paragraphs[i].remove
        i += 1
        while i < paragraphs.length && !paragraphs[i].text.to_s.include?("</details>")
          paragraphs[i].remove
          i += 1
        end
        paragraphs[i].remove if i < paragraphs.length
        break
      end
      i += 1
    end
  end

  on(:post_created) do |post, _opts, user|
    next unless SiteSetting.gemini_enabled
    next if user.blank? || deep_research_bot?(user)
    raw = post.raw.to_s

    if raw =~ /\A@local-deep-research[:\s]+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_local_job(user, post, { topic: $1.strip })
    elsif raw =~ /\A\/ldr\s+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_local_job(user, post, { topic: $1.strip })
    elsif raw =~ /\A@deep-research[:\s]+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_job(user, post, { topic: $1.strip })
    elsif raw =~ /\A\/deep\s+(.+)\z/m && SiteSetting.gemini_deep_research_enabled
      DiscourseGemini.run_job(user, post, { topic: $1.strip })
    end

    # built-in chat bots: mention-triggered replies
    next unless SiteSetting.gemini_chat_enabled
    next if post.custom_fields["gemini_chat_reply_enqueued"].present?

    mentioned = raw.scan(/@([a-zA-Z0-9_\-]+)/).flatten.uniq
    mentioned.each do |username|
      model = DiscourseGemini.bot_model(username)
      next if model.blank?
      next unless DiscourseGemini.allowed_for?(user)
      if DiscourseGemini.remaining_uses(user) < 1
        Jobs.enqueue(:gemini_notice, post_id: post.id, text: I18n.t("discourse_gemini.rate_limited"))
        next
      end
      DiscourseGemini.record_use(user, 1)
      post.custom_fields["gemini_chat_reply_enqueued"] = true
      post.save_custom_fields
      Jobs.enqueue(:gemini_chat_reply, post_id: post.id, bot_username: username)
      break
    end
  end
end

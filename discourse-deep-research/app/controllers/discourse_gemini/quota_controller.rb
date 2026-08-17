# frozen_string_literal: true

module DiscourseGemini
  # Sloth AI admin page — quota monitor + plugin settings on one page.
  #
  # Serves:
  #   GET  /sloth-ai                        → public quota/status page
  #   GET  /admin/plugins/sloth-ai (and /full) → admin: settings + quota
  #   POST /admin/plugins/sloth-ai/settings → admin: save settings
  #
  # Quota comes from the bridge's /api/quota (Antigravity + OpenCode usage).
  class QuotaController < ::ApplicationController
    skip_before_action :redirect_to_login_if_required
    skip_before_action :check_xhr
    layout "no_ember" # plain page — the Ember app must NOT boot on these URLs

    SETTING_KEYS = %w[
      gemini_enabled gemini_bridge_url gemini_bridge_token
      gemini_opencode_api_key gemini_model
      gemini_daily_limit_per_user gemini_chat_history_posts
      gemini_allowed_groups gemini_deep_research_enabled
      gemini_deep_research_max_questions gemini_chat_enabled
      gemini_bot_username
    ].freeze

    def index
      # /admin/plugins/sloth-ai (and /full) = admin page with settings + bot
      # management; /sloth-ai = public quota monitor for everyone.
      @admin_page = request.path.start_with?("/admin/")
      @quota = GeminiBridge.new.quota
      @models = GeminiBridge.new.models
      @error = nil
      @settings = @admin_page && current_user&.admin? ? plugin_settings : []
      @saved = flash[:sloth_saved].present?
      @save_error = flash[:sloth_error]
    rescue StandardError => e
      Rails.logger.error("[sloth-debug] index error: #{e.class}: #{e.message}")
      Rails.logger.error(e.backtrace.first(6).join(" | "))
      @quota = nil
      @error = e.message
    end

    def update_settings
      raise Discourse::InvalidAccess unless current_user&.admin?

      if params[:opencode_api_key].present?
        SiteSetting.gemini_opencode_api_key = params[:opencode_api_key].to_s.strip
      end
      if params[:bridge_token].present?
        SiteSetting.gemini_bridge_token = params[:bridge_token].to_s.strip
      end
      if params[:bridge_url].present?
        SiteSetting.gemini_bridge_url = params[:bridge_url].to_s.strip
      end
      if params[:model].present?
        SiteSetting.gemini_model = params[:model].to_s.strip
      end
      if params[:daily_limit].present?
        SiteSetting.gemini_daily_limit_per_user = params[:daily_limit].to_i
      end
      if params[:history_posts].present?
        SiteSetting.gemini_chat_history_posts = params[:history_posts].to_i
      end
      if params[:allowed_groups].present?
        SiteSetting.gemini_allowed_groups = params[:allowed_groups].to_s.strip
      end

      flash[:sloth_saved] = true
    rescue StandardError => e
      flash[:sloth_error] = e.message
    ensure
      redirect_to "/admin/plugins/sloth-ai"
    end

    # POST /admin/plugins/sloth-ai/bots?model[]=<id>&model[]=<id>...
    # Save the ENABLED set of models: checked models become summonable bots
    # (username ai_<model>, renaming any old-style ai_<model_with_underscores>
    # and rewriting @mentions in existing posts). Unchecked models are
    # disabled — removed from the config so mentions no longer trigger a
    # reply; their bot user is deleted if it has no posts.
    def create_bots
      raise Discourse::InvalidAccess unless current_user&.admin?

      models = Array(params[:model]).map(&:to_s).reject(&:blank?).uniq
      bridge = GeminiBridge.new
      old_config = DiscourseGemini.bot_config
      # Candidate set = everything the bridge lists + models already in the
      # config (plain names like gemini-3.6-flash are not in the quota list,
      # which only exposes tiered variants).
      available = (bridge.models["antigravity"] || []).map { |m| m["id"] } +
                  (bridge.models["opencode"] || []).map { |m| m["id"] } +
                  old_config.values
      available.uniq!

      created = 0
      renamed = 0
      errors = []
      new_config = {}
      models.each do |model_id|
        next unless available.include?(model_id)

        # taken = old config + models already assigned in this save cycle,
        # so bare/tiered variants never collide on the same bot username.
        bot_name = DiscourseGemini.bot_username_for(model_id, taken: old_config.merge(new_config))
        unless DiscourseGemini.valid_bot_username?(bot_name)
          # keep the previous (short) name if this model was enabled before,
          # e.g. ai_gemini_image for gemini-3.1-flash-image
          existing = old_config.key(model_id)
          if existing
            new_config[existing] = model_id
          else
            errors << "#{model_id}（帳號 #{bot_name} 超過 #{SiteSetting.max_username_length} 字上限，未啟用）"
          end
          next
        end

        # 1) ensure the bot user exists under the new naming
        bot = User.find_by(username: bot_name)
        if bot.blank?
          old_bot = DiscourseGemini.bot_user_for_model(model_id)
          if old_bot
            old_bot.change_username(bot_name, Discourse.system_user)
            renamed += 1
          else
            DiscourseGemini.create_bot_user!(bot_name)
            created += 1
          end
        end

        new_config[bot_name] = model_id
      end

      # 2) cleanup: remove bot users that are no longer enabled and have no
      # posts (disabled models + old-style orphans re-created by a previous
      # ensure_chat_bots! boot). Users with posts are kept (real content).
      orphan_names =
        (old_config.keys + User.where("username LIKE 'ai_%'").pluck(:username)).uniq -
        new_config.keys
      orphan_names.each do |name|
        u = User.find_by(username: name)
        next unless u && u.posts.count.zero?
        u.destroy
      end

      SiteSetting.gemini_bot_models = JSON.generate(new_config)
      msg = "✅ Bots 已儲存：啟用 #{new_config.size} 個模型（新增 #{created}、改名 #{renamed}）"
      msg += "；跳過 #{errors.size} 個：#{errors.join('；')}" if errors.any?
      flash[:sloth_saved] = msg
    rescue StandardError => e
      flash[:sloth_error] = e.message
    ensure
      redirect_to "/admin/plugins/sloth-ai"
    end

    private

    def plugin_settings
      # Settings are hidden from the native admin page via the
      # hidden_site_settings modifier — load them here explicitly.
      SiteSetting.all_settings(include_hidden: true)
                      .select { |s| SETTING_KEYS.include?(s[:setting].to_s) }
    rescue StandardError
      []
    end
  end
end
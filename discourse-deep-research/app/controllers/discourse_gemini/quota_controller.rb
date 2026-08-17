# frozen_string_literal: true

module DiscourseGemini
  # Sloth AI admin page — quota monitor + plugin settings on one page.
  #
  # Serves:
  #   GET  /quota                          → public quota-only page
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
      gemini_opencode_api_key gemini_model gemini_bot_models
      gemini_daily_limit_per_user gemini_chat_history_posts
      gemini_allowed_groups gemini_deep_research_enabled
      gemini_deep_research_max_questions gemini_chat_enabled
      gemini_bot_username
    ].freeze

    def index
      @quota = GeminiBridge.new.quota
      @error = nil
      @settings = current_user && current_user.admin? ? plugin_settings : []
      @saved = flash[:sloth_saved].present?
      @save_error = flash[:sloth_error]
    rescue StandardError => e
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
      if params[:bot_models].present?
        JSON.parse(params[:bot_models].to_s)
        SiteSetting.gemini_bot_models = params[:bot_models].to_s.strip
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
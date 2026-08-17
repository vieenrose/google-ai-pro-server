# frozen_string_literal: true

module DiscourseGemini
  # Sloth AI admin page — quota monitor + plugin settings on one page.
  #
  # Serves:
  #   GET  /quota                          → public quota-only page
  #   GET  /admin/plugins/antigravity-quota (and /full) → admin: settings + quota
  #   POST /admin/plugins/antigravity-quota/settings → admin: save settings
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
      # only update URL if a non-default value was submitted
      if params[:bridge_url].present? && params[:bridge_url] != "http://127.0.0.1:8787"
        SiteSetting.gemini_bridge_url = params[:bridge_url].to_s.strip
      end

      flash[:sloth_saved] = true
    rescue StandardError => e
      flash[:sloth_error] = e.message
    ensure
      redirect_to "/admin/plugins/antigravity-quota"
    end

    private

    def plugin_settings
      all = SiteSetting.all_settings
      settings = all + Plugin::Instance.settings
      settings.select { |s| SETTING_KEYS.include?(s[:setting].to_s) }
    rescue StandardError
      []
    end
  end
end
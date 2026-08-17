# frozen_string_literal: true

module DiscourseGemini
  # Sloth AI — minimal admin page: Google AI Pro subscription status +
  # quota monitor. The plugin now only provides the AGY-OCI bridge to the
  # forum (quota + subscription management); models/bots are handled by
  # Discourse AI pointing at the bridge.
  class QuotaController < ::ApplicationController
    skip_before_action :redirect_to_login_if_required
    skip_before_action :check_xhr
    layout "no_ember"

    def index
      @admin_page = request.path.start_with?("/admin/")
      @quota = GeminiBridge.new.quota
      @error = nil
      @auth_status = @admin_page ? GeminiBridge.new.antigravity_auth_status : nil
      @reauth_url = flash[:sloth_reauth_url]
      @reauth_verifier = flash[:sloth_reauth_verifier]
      @reauth_ok = flash[:sloth_reauth_ok]
      @reauth_error = flash[:sloth_reauth_error]
    rescue StandardError => e
      @quota = nil
      @error = e.message
    end

    # POST /admin/plugins/sloth-ai/reauth — start Google AI Pro re-auth.
    def reauth_url
      raise Discourse::InvalidAccess unless current_user&.admin?
      data = GeminiBridge.new.antigravity_auth_url
      if data["auth_url"].present?
        flash[:sloth_reauth_url] = data["auth_url"]
        flash[:sloth_reauth_verifier] = data["verifier"].to_s
      else
        flash[:sloth_reauth_error] = data["error"] || "無法取得認證連結"
      end
    ensure
      redirect_to "/admin/plugins/sloth-ai"
    end

    # POST /admin/plugins/sloth-ai/reauth/exchange — complete re-auth.
    def reauth_exchange
      raise Discourse::InvalidAccess unless current_user&.admin?
      code = params[:code].to_s.strip
      verifier = params[:verifier].to_s.strip
      if code.present?
        result = GeminiBridge.new.antigravity_auth_exchange(code, verifier)
        if result["ok"]
          flash[:sloth_reauth_ok] = "✅ 重新認證成功#{result["account"] ? "（#{result["account"]}）" : ""}。"
        else
          flash[:sloth_reauth_error] = result["error"] || "交換失敗"
        end
      else
        flash[:sloth_reauth_error] = "請貼上 Google 回傳的 code"
      end
    ensure
      redirect_to "/admin/plugins/sloth-ai"
    end
  end
end

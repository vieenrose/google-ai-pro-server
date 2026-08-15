# frozen_string_literal: true

module DiscourseGemini
  # Public quota monitor: shows per-model Antigravity quota (remaining % and
  # reset countdown) so all users can see which models are available and how
  # long a limited model takes to renew. Data comes from the bridge's
  # /api/quota (Antigravity control plane quotaInfo).
  class QuotaController < ::ApplicationController
    skip_before_action :redirect_to_login_if_required
    skip_before_action :check_xhr
    layout "no_ember" # plain page — the Ember app must NOT boot on these URLs

    def index
      @quota = GeminiBridge.new.quota
      @error = nil
    rescue StandardError => e
      @quota = nil
      @error = e.message
    end
  end
end

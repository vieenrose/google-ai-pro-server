# frozen_string_literal: true

module DiscourseGemini
  class AdminQuotaController < Admin::AdminController
    skip_before_action :check_xhr

    def index
      @quota = GeminiBridge.new.quota
      @error = nil
    rescue StandardError => e
      @quota = nil
      @error = e.message
    end
  end
end

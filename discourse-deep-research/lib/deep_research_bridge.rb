# frozen_string_literal: true

require "net/http"
require "json"

# Thin HTTP client for the local Sloth AI bridge.
# The bridge wraps your Google AI Pro subscription (Antigravity) and exposes
# chat / quota / auth-status for the Sloth AI admin page.
class GeminiBridge
  class Error < StandardError; end

  def initialize(url: nil, token: nil)
    @url = (url || SiteSetting.gemini_bridge_url).to_s.sub(%r{/+\z}, "")
    @token = token.nil? ? SiteSetting.gemini_bridge_token.to_s : token.to_s
  end

  # GET /api/quota → { fetched_at:, models: [{key, name, remaining, reset_time}] }
  def quota
    get("/api/quota")
  end

  # GET /api/models → { antigravity: [{id,name}] }
  def models
    get("/api/models")
  end

  # GET /v1/config/antigravity-auth → auth/subscription status
  def antigravity_auth_status
    get("/v1/config/antigravity-auth")
  end

  # POST /v1/config/antigravity-auth/url → { auth_url:, verifier: }
  def antigravity_auth_url
    post("/v1/config/antigravity-auth/url", {})
  end

  # POST /v1/config/antigravity-auth/exchange → { ok:, account:, error: }
  def antigravity_auth_exchange(code, verifier)
    post("/v1/config/antigravity-auth/exchange", { code: code, verifier: verifier })
  end

  def healthy?
    get("/health")["ok"] == true
  rescue StandardError
    false
  end

  private

  def get(path)
    uri = URI.join("#{@url}/", path)
    req = Net::HTTP::Get.new(uri)
    req["Authorization"] = "Bearer #{@token}" unless @token.empty?
    JSON.parse(request(req).body)
  end

  def post(path, payload, read_timeout: 60)
    uri = URI.join("#{@url}/", path)
    req = Net::HTTP::Post.new(uri)
    req["Content-Type"] = "application/json"
    req["Authorization"] = "Bearer #{@token}" unless @token.empty?
    req.body = JSON.dump(payload)
    JSON.parse(request(req, read_timeout: read_timeout).body)
  end

  def request(req, read_timeout: 60)
    uri = req.uri
    Net::HTTP.start(uri.host, uri.port, read_timeout: read_timeout, open_timeout: 10) do |http|
      http.request(req)
    end
  rescue Net::OpenTimeout, Net::ReadTimeout => e
    raise Error, e.message
  end
end

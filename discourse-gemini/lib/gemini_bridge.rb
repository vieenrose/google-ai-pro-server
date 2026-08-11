# frozen_string_literal: true

require "net/http"
require "json"

# Thin HTTP client for the local Gemini bridge (bridge/server.py).
# The bridge wraps the Antigravity CLI (agy) which consumes the Google AI Pro
# subscription — chat replies and Deep Research reports come from real,
# search-grounded Gemini.
class GeminiBridge
  class Error < StandardError; end

  def initialize(url: nil, token: nil)
    @url = (url || SiteSetting.gemini_bridge_url).to_s.sub(%r{/+\z}, "")
    @token = token.nil? ? SiteSetting.gemini_bridge_token.to_s : token.to_s
  end

  # POST /v1/chat → { reply:, model:, usage:, error: }
  def chat(messages, model: nil)
    post("/v1/chat", { messages: messages, model: model }.compact)
  end

  # POST /v1/deep-research → { report:, sources:, plan:, error: }
  # ⚠ Slow (1–5 minutes). Only call from a background job.
  def deep_research(topic, max_questions: nil, model: nil)
    post("/v1/deep-research", {
      topic: topic,
      max_questions: max_questions || SiteSetting.gemini_deep_research_max_questions,
      model: model || SiteSetting.gemini_model,
    }.compact)
  end

  def healthy?
    get("/health")["ok"] == true
  rescue StandardError
    false
  end

  private

  def get(path)
    req = Net::HTTP::Get.new(path)
    request(req)
  end

  def post(path, payload)
    req = Net::HTTP::Post.new(path)
    req["Content-Type"] = "application/json"
    req.body = JSON.dump(payload)
    request(req)
  end

  def request(req)
    uri = URI.join("#{@url}/", req.path)
    req["Authorization"] = "Bearer #{@token}" unless @token.empty?
    res = Net::HTTP.start(uri.host, uri.port, read_timeout: 600, open_timeout: 10) do |http|
      http.request(req)
    end
    body = JSON.parse(res.body) rescue {}
    raise Error, "bridge HTTP #{res.code}: #{body["error"] || res.body}" unless res.is_a?(Net::HTTPSuccess)
    body
  end
end

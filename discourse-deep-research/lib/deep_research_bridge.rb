# frozen_string_literal: true

require "net/http"
require "json"

# Thin HTTP client for the local bridge (bridge/server.py).
# The bridge wraps your Google AI Pro subscription (via the Gemini API or the
# Antigravity CLI) and exposes /v1/deep-research for the Deep Research plugin.
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

  # GET /api/quota → { fetched_at:, models: [{key, name, remaining, reset_time}] }
  def quota
    get("/api/quota")
  end

  # POST /v1/local-deep-research → { report:, sources:, duration_seconds: }
  # Self-hosted Local Deep Research (2–10 minutes) — long read timeout.
  def local_deep_research(topic)
    post("/v1/local-deep-research", { topic: topic }, read_timeout: 1200)
  end

  # POST /v1/chat/completions (OpenAI-compatible, SSE stream). Yields each
  # assistant text delta. Returns the full accumulated content.
  def stream_chat_completions(messages, model:)
    buffer = +""
    uri = URI.join("#{@url}/", "/v1/chat/completions")
    req = Net::HTTP::Post.new(uri.path)
    req["Content-Type"] = "application/json"
    req["Authorization"] = "Bearer #{@token}" unless @token.empty?
    req.body = JSON.dump({ messages: messages, model: model, stream: true })

    Net::HTTP.start(uri.host, uri.port, read_timeout: 600, open_timeout: 10) do |http|
      http.request(req) do |resp|
        unless resp.is_a?(Net::HTTPSuccess)
          raise Error, "bridge HTTP #{resp.code}: #{resp.body.to_s[0, 300]}"
        end
        resp.read_body do |chunk|
          chunk.split("\n").each do |line|
            next unless line.start_with?("data: ")
            data = line.delete_prefix("data: ").strip
            next if data == "[DONE]"
            begin
              parsed = JSON.parse(data)
            rescue JSON::ParserError
              next
            end
            (parsed.dig("choices") || []).each do |choice|
              delta = choice.dig("delta", "content")
              next if delta.blank?
              buffer << delta
              yield delta
            end
          end
        end
      end
    end
    buffer
  end

  private

  def get(path)
    req = Net::HTTP::Get.new(path)
    request(req)
  end

  def post(path, payload, read_timeout: 600)
    req = Net::HTTP::Post.new(path)
    req["Content-Type"] = "application/json"
    req.body = JSON.dump(payload)
    request(req, read_timeout: read_timeout)
  end

  def request(req, read_timeout: 600)
    uri = URI.join("#{@url}/", req.path)
    req["Authorization"] = "Bearer #{@token}" unless @token.empty?
    res = Net::HTTP.start(uri.host, uri.port, read_timeout: read_timeout, open_timeout: 10) do |http|
      http.request(req)
    end
    body = JSON.parse(res.body) rescue {}
    raise Error, "bridge HTTP #{res.code}: #{body["error"] || res.body}" unless res.is_a?(Net::HTTPSuccess)
    body
  end
end

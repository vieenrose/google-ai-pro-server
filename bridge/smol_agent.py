"""smol_agent.py — smolagents ToolCallingAgent with a SearXNG tool.

Experiment comparing the HuggingFace smolagents framework against the
stdlib-only agent loop in agent.py. Uses the OpenCode Go OpenAI-compatible
endpoint (deepseek-v4-flash etc.).

Usage:
    python3 smol_agent.py "今天台灣的重要新聞有哪些？"
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

from smolagents import CodeAgent, ToolCallingAgent, tool
from smolagents.models import OpenAIServerModel

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
OPENCODE_BASE = os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
OPENCODE_KEY = os.environ.get("OPENCODE_API_KEY", "")
MAX_RESULTS = int(os.environ.get("SEARXNG_MAX_RESULTS", "5"))


def _searx(query: str, language: str = "") -> list[dict]:
    params = urllib.parse.urlencode({"q": query.strip()[:200], "format": "json"})
    if language:
        params += "&" + urllib.parse.urlencode({"language": language})
    req = urllib.request.Request(
        f"{SEARXNG_URL}/search?{params}",
        headers={"User-Agent": "gemini-bridge/1.0 (Discourse forum)"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode(errors="replace"))
    out = []
    for item in data.get("results") or []:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": url,
                "content": " ".join((item.get("content") or "").split())[:250],
            }
        )
    return out


@tool
def web_search(query: str) -> str:
    """Search the web via a self-hosted SearXNG instance. Returns numbered
    results as 'title — url: content' lines. Use this for any question that
    needs current or factual information.

    Args:
        query: The search keywords or question to look up on the web.
    """
    if not query.strip():
        return "empty query"
    results: list[dict] = []
    seen: set[str] = set()
    # Short proven-good fallback keywords for common intents — long
    # question-style queries return dictionary/calendar noise from SearXNG,
    # short keyword queries return proper news/source sites. Fallbacks lead.
    intent_fallbacks = {
        "新聞": "台灣新聞",
        "news": "Taiwan news",
        "天氣": "台灣天氣",
        "weather": "Taiwan weather",
        "颱風": "颱風 台灣",
        "台股": "台股",
        "股價": "NVDA stock price",
    }
    variants = []
    for key, kw in intent_fallbacks.items():
        if key.lower() in query.lower():
            variants.append(kw)
    variants.append(query.strip())
    if "新聞" in query or "news" in query.lower():
        variants.append(query.strip() + " 新聞")
    for v in variants:
        try:
            for r in _searx(v):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    results.append(r)
        except Exception:
            continue
    if not results:
        return "no results"
    lines = []
    for i, r in enumerate(results[:MAX_RESULTS], 1):
        lines.append(f"{i}. {r['title']} — {r['url']}\n   {r['content']}")
    return "\n".join(lines)


def run(question: str, model_id: str = "deepseek-v4-flash", agent_type: str = "code", context: str = "", base_url: str = "", api_key: str = "") -> dict:
    model = OpenAIServerModel(
        model_id=model_id,
        api_base=base_url or OPENCODE_BASE,
        api_key=api_key or OPENCODE_KEY or "none",
    )
    if agent_type == "tools":
        agent = ToolCallingAgent(tools=[web_search], model=model, max_steps=3, verbosity_level=0)
    else:
        agent = CodeAgent(tools=[web_search], model=model, max_steps=3, verbosity_level=0)
    task = question
    if context.strip():
        task = (
            "以下是同一討論串中先前的訊息（供你理解上下文，不需重述）：\n\n"
            + context.strip()
            + "\n\n請根據以上內容回答使用者的最新問題：\n"
            + question
        )
    answer = agent.run(task)
    return {"answer": str(answer), "steps": []}


def _format_context(messages: list[dict], max_chars: int = 8000) -> str:
    """Build a readable transcript of prior messages for the agent.

    Excludes the last user turn (that's the live question). Strips @mentions
    (forum routing) and the "author: " prefixes the plugin adds.
    """
    if not messages:
        return ""
    lines = []
    total = 0
    for m in messages:
        if total >= max_chars:
            break
        role = "使用者" if m.get("role") == "user" else "AI"
        c = m.get("content") or ""
        if isinstance(c, list):
            c = " ".join(
                el.get("text", "") for el in c
                if isinstance(el, dict) and el.get("type") == "text"
            )
        c = re.sub(r"@[A-Za-z0-9_\-]+", " ", str(c))
        c = re.sub(r"^\s*[\w.@\-]+\s*:\s*", " ", c)  # "author: " prefix
        c = " ".join(c.split())
        if not c.strip():
            continue
        line = f"{role}: {c}"
        if total + len(line) > max_chars:
            line = line[: max_chars - total]
        lines.append(line)
        total += len(line)
    return "\n\n".join(lines)


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "今天台灣的重要新聞有哪些？"
    agent_type = sys.argv[2] if len(sys.argv) > 2 else "code"
    # optional third arg: JSON-encoded message list for thread context
    context = ""
    if len(sys.argv) > 3 and sys.argv[3]:
        try:
            import json as _json
            context = _format_context(_json.loads(sys.argv[3]))
        except Exception:
            context = sys.argv[3]
    # optional 4th/5th args: base_url / api_key for non-OpenCode providers
    # (Together AI etc.)
    base_url = sys.argv[4] if len(sys.argv) > 4 else ""
    api_key = sys.argv[5] if len(sys.argv) > 5 else ""
    model_id = sys.argv[6] if len(sys.argv) > 6 else "deepseek-v4-flash"
    print(run(q, agent_type=agent_type, context=context, base_url=base_url, api_key=api_key, model_id=model_id)["answer"])

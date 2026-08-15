#!/usr/bin/env python3
"""searxng.py — bridge-side web search via the host's self-hosted SearXNG.

Used to give web access to models that have no native grounding (e.g. the
Claude models on the Antigravity app backend, or future OpenCode Go models).
Gemini models keep their native googleSearch tool and do not go through this
path.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
SEARXNG_LANG = os.environ.get("SEARXNG_LANG", "zh-TW")
SEARXNG_MAX_RESULTS = int(os.environ.get("SEARXNG_MAX_RESULTS", "5"))
SEARXNG_TIMEOUT = float(os.environ.get("SEARXNG_TIMEOUT", "8"))


def search(query: str, max_results: int | None = None) -> list[dict]:
    """Query SearXNG and return [{title, url, content}]. Empty on any failure.

    The bridge treats this as best-effort: if the instance is down or slow the
    model simply answers from training knowledge.
    """
    if not SEARXNG_URL or not query.strip():
        return []
    max_results = max_results or SEARXNG_MAX_RESULTS
    params = urllib.parse.urlencode(
        {"q": query.strip()[:200], "format": "json", "language": SEARXNG_LANG}
    )
    req = urllib.request.Request(
        f"{SEARXNG_URL}/search?{params}",
        headers={"User-Agent": "gemini-bridge/1.0 (Discourse forum)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=SEARXNG_TIMEOUT) as response:
            data = json.loads(response.read().decode(errors="replace"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[bridge] searxng search failed: {e}\n")
        return []
    results = []
    for item in (data.get("results") or [])[:max_results]:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = re.sub(r"\s+", " ", item.get("content") or "").strip()[:300]
        if url:
            results.append({"title": title or url, "url": url, "content": content})
    return results


def last_user_query(messages: list[dict]) -> str:
    """Extract the last user text from an OpenAI-style message list."""
    for message in reversed(messages or []):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts = [
                el.get("text", "")
                for el in content
                if isinstance(el, dict) and el.get("type") == "text"
            ]
            if texts and texts[-1].strip():
                return texts[-1].strip()
    return ""


def inject(messages: list[dict], model: str) -> tuple[list[dict], list[dict]]:
    """Insert SearXNG results before the last user message when the model has
    no native grounding (anything that is not a Gemini model).

    Returns (new_messages, injected_results). injected_results is [] when no
    search was made.
    """
    model = (model or "").lower()
    if model.startswith("gemini-"):
        return messages, []
    if not SEARXNG_URL:
        return messages, []
    query = last_user_query(messages)
    if len(query) < 4:
        return messages, []
    results = search(query)
    if not results:
        return messages, []

    block = (
        "以下是即時網路搜尋結果（僅供參考；回答時如需使用請以 [1][2]… 引用，"
        "不要提及「搜尋結果」本身）：\n"
        + "\n".join(
            f"[{i}] {r['title']} — {r['url']}\n    {r['content']}"
            for i, r in enumerate(results, 1)
        )
    )
    out = list(messages)
    out.insert(len(out) - 1, {"role": "user", "content": block})
    return out, results


def sources_markdown(results: list[dict]) -> str:
    """Append a Sources section for the injected results."""
    if not results:
        return ""
    return "\n\n### Sources\n" + "\n".join(
        f"- [{r['title']}]({r['url']})" for r in results
    )

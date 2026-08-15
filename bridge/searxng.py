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


def search(query: str, max_results: int | None = None, language: str | None = None) -> list[dict]:
    """Query SearXNG and return [{title, url, content}]. Empty on any failure.

    The bridge treats this as best-effort: if the instance is down or slow the
    model simply answers from training knowledge.
    """
    if not SEARXNG_URL or not query.strip():
        return []
    max_results = max_results or SEARXNG_MAX_RESULTS
    params = {"q": query.strip()[:200], "format": "json"}
    if language is None:
        language = SEARXNG_LANG
    if language:
        params["language"] = language
    query_string = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{SEARXNG_URL}/search?{query_string}",
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
    """Extract the last user text from an OpenAI-style message list.

    @mentions are stripped — they are routing instructions for the forum bots,
    not search terms, and they pollute SearXNG results.
    """
    for message in reversed(messages or []):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            c = re.sub(r"@[A-Za-z0-9_\-]+", " ", content)
            c = re.sub(r"^\s*[\w.@\-]+\s*:\s*", " ", c)  # "author: " prefix
            return c.strip()
        if isinstance(content, list):
            texts = [
                el.get("text", "")
                for el in content
                if isinstance(el, dict) and el.get("type") == "text"
            ]
            if texts and texts[-1].strip():
                c = re.sub(r"@[A-Za-z0-9_\-]+", " ", texts[-1])
                c = re.sub(r"^\s*[\w.@\-]+\s*:\s*", " ", c)
                return c.strip()
    return ""


def inject(messages: list[dict], model: str, search_query: str = "") -> tuple[list[dict], list[dict]]:
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
    # Prefer a model-generated keyword query when provided; otherwise clean
    # the raw question (strip instruction phrasing).
    cleaned = (search_query or "").strip().strip('"').strip("'").strip()
    if len(cleaned) < 4:
        cleaned = re.sub(
            r"(請搜尋|請|列出|簡短|就好|就好$|三則|則$|。|！|？|,|，|、|\s)",
            " ",
            query,
        )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 4:
        cleaned = query
    is_news = ("新聞" in query or "news" in query.lower())
    # Unfiltered global search leads: the zh-TW language filter tends to
    # return dictionary/calendar noise; good Taiwanese sources (BBC, TVBS,
    # Taipei Times, Google News) surface better without it.
    results = search(cleaned, language="")
    for r in search(cleaned) or []:  # zh-TW-filtered variant as fallback
        if r["url"] not in {x["url"] for x in results}:
            results.append(r)
    if is_news:
        extra = cleaned if "新聞" in cleaned else cleaned + " 新聞"
        for r in search(extra, language="") or []:
            if r["url"] not in {x["url"] for x in results}:
                results.insert(0, r)
    results = results[:SEARXNG_MAX_RESULTS]
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

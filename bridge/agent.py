"""agent.py — lightweight web-grounded agent for models without native search.

Loop (max 2 search rounds, stdlib only):
  1. the model turns the user question into search-engine keywords
  2. SearXNG search (fallback: cleaned raw question)
  3. the model may issue ONE refined search query if the results look off
  4. final answer is streamed with a results block + a Sources section

Used for OpenCode Go models (deepseek/mimo/glm/...). Gemini models keep
their native googleSearch grounding and do not go through this module.
"""

from __future__ import annotations

import re

from searxng import search, sources_markdown, SEARXNG_MAX_RESULTS

MAX_KEYWORDS = 40

_KEYWORD_PROMPT = (
    "把以下問題轉成適合搜尋引擎的關鍵字查詢（5～15字）。"
    "只輸出關鍵字本身，不要標點、不要引號、不要解釋。\n\n問題：{q}"
)

_REFINE_PROMPT = (
    "以下是使用者問題與目前的搜尋結果摘要。判斷這些結果是否足以回答問題。"
    "若足夠，只輸出單字「ANSWER」。若不夠，輸出「SEARCH: 」後接新的搜尋關鍵字"
    "（5～15字，只輸出關鍵字本身）。\n\n"
    "問題：{q}\n\n搜尋結果：\n{results}"
)

_ANSWER_SYSTEM = (
    "你是論壇 AI 助理。請根據提供的即時網路搜尋結果回答使用者問題；"
    "引用結果時使用 [1][2]… 標記。若結果不足以回答，就誠實說明並給建議。"
    "不要提及「搜尋結果」這四個字本身。"
)


def _cleaned_question(question: str) -> str:
    q = re.sub(r"(請搜尋|請列出|簡短|列出|就好|則$|。|！|？|,|，|、)", " ", question)
    return re.sub(r"\s+", " ", q).strip() or question


def _generate_keywords(backend, model: str, question: str) -> str:
    try:
        result = backend.chat_messages(
            [{"role": "user", "content": _KEYWORD_PROMPT.format(q=question)}],
            model=model,
        )
        kw = (result.text or "").strip().strip('"').strip("'").strip()
        return kw[:MAX_KEYWORDS]
    except Exception:
        return ""


def _refine_query(backend, model: str, question: str, results: list[dict]) -> str:
    if not results:
        return ""
    digest = "\n".join(
        f"- {r['title'][:80]} {r['content'][:120]}" for r in results[:5]
    )
    try:
        result = backend.chat_messages(
            [{"role": "user", "content": _REFINE_PROMPT.format(q=question, results=digest)}],
            model=model,
        )
        text = (result.text or "").strip()
    except Exception:
        return ""
    if text.upper().startswith("ANSWER"):
        return ""
    if text.upper().startswith("SEARCH:"):
        return text.split(":", 1)[1].strip()[:MAX_KEYWORDS]
    return ""


def _format_results(results: list[dict]) -> str:
    return "\n".join(
        f"[{i}] {r['title']} — {r['url']}\n    {r['content']}"
        for i, r in enumerate(results, 1)
    )


def web_answer_stream(backend, messages, model):
    """Yield answer text deltas (final Sources markdown included)."""
    question = ""
    for m in reversed(messages or []):
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            question = c.strip()
            break
    if not question:
        yield from _passthrough(backend, messages, model)
        return

    keywords = _generate_keywords(backend, model, question) or _cleaned_question(question)
    results = search(keywords)
    if not results:
        results = search(_cleaned_question(question))

    refined = _refine_query(backend, model, question, results)
    if refined:
        extra = search(refined) or []
        for r in extra:
            if r["url"] not in {x["url"] for x in results}:
                results.append(r)
        results = results[:SEARXNG_MAX_RESULTS]

    if not results:
        yield from _passthrough(backend, messages, model)
        return

    grounded = [{"role": "system", "content": _ANSWER_SYSTEM}]
    grounded += [
        {
            "role": "user",
            "content": "即時網路搜尋結果：\n" + _format_results(results),
        }
    ]
    grounded += list(messages)
    for delta, _meta in backend.chat_messages_stream(grounded, model=model):
        if delta:
            yield delta
    yield sources_markdown(results)


def web_answer(backend, messages, model) -> str:
    """Non-streaming variant — accumulates the streamed answer."""
    buffer = []
    for delta in web_answer_stream(backend, messages, model):
        buffer.append(delta)
    return "".join(buffer)


def _passthrough(backend, messages, model):
    for delta, _meta in backend.chat_messages_stream(messages, model=model):
        if delta:
            yield delta

#!/usr/bin/env python3
"""
deep_research.py — Deep Research workflow on the Google AI Pro subscription.

Reality check (verified against Google docs, 2026):
  * The *true* Gemini Deep Research agent is NOT exposed through the AI Pro
    subscription — it only exists in the Gemini app (not scriptable) or as a
    PAID Gemini API (Interactions API).
  * What the subscription DOES give us is an agentic model with Google Search
    grounding, web reading and code tools (via agy / the Antigravity backend).

So this module runs a **Deep-Research-style workflow** on that backend:

    Phase 1  PLAN       — ask the agent for a focused research plan + numbered
                          questions.
    Phase 2  GATHER     — for each question, run a grounded research pass and
                          collect notes with citations.
    Phase 3  SYNTHESIZE — one final pass writes the cited report.

The output is a decent approximation of Deep Research. If you later obtain a
paid Gemini API key, swap this module for the official Interactions API
(client.interactions.create) with no CLI changes.
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field

RESEARCH_SYSTEM = (
    "You are a research analyst. You produce thorough, well-structured research "
    "with numbered citations. When asked to research, you use web search and "
    "read the sources you find. Always cite sources inline as [1], [2], ... and "
    "append a '## Sources' section with the URLs."
)

PLAN_PROMPT = (
    "Create a focused research plan for the following topic. "
    "Return ONLY a numbered list of 2 to 5 concrete research questions "
    "(one per line, format '1. question'), nothing else.\n\nTopic: {topic}"
)

GATHER_PROMPT = (
    "Research the following question using web search. Read the sources you "
    "find and write detailed notes with inline citations [n] and a '## Sources' "
    "list with URLs.\n\nQuestion: {question}"
)

SYNTHESIZE_PROMPT = (
    "You are writing the final report for a research project. Based ONLY on the "
    "research notes below, write a comprehensive, well-structured report with "
    "sections, inline citations [n], and a final '## Sources' list. Preserve all "
    "citations from the notes.\n\n--- RESEARCH NOTES ---\n\n{notes}"
)


@dataclass
class ResearchResult:
    topic: str
    report: str = ""
    plan: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)  # [{question, notes}]
    error: str = ""
    phases_run: int = 0

    @property
    def sources(self) -> list[str]:
        urls = []
        for item in self.notes:
            for url in re.findall(r"https?://[^\s\)\]\"']+", item.get("notes", "")):
                if url not in urls:
                    urls.append(url)
        for url in re.findall(r"https?://[^\s\)\]\"']+", self.report):
            if url not in urls:
                urls.append(url)
        return urls


def run_research(backend, topic: str, max_questions: int = 3,
                 model: str = "gemini-3.5-flash",
                 progress=None) -> ResearchResult:
    """Run the 3-phase research workflow on the given backend.

    progress: optional callable(message: str) invoked between phases.
    """
    result = ResearchResult(topic=topic)
    if progress:
        progress(f"Phase 1/3 — planning research on: {topic}")

    # ── Phase 1: plan ────────────────────────────────────────────────────────
    plan_res = backend.chat(PLAN_PROMPT.format(topic=topic), model=model)
    if plan_res.error:
        result.error = f"planning failed: {plan_res.error}"
        return result
    result.plan = _extract_questions(plan_res.text)
    if not result.plan:
        # fall back to treating the whole plan as one question
        result.plan = [topic]
    result.phases_run = 1

    # ── Phase 2: gather ──────────────────────────────────────────────────────
    for i, q in enumerate(result.plan[:max_questions], 1):
        if progress:
            progress(f"Phase 2/3 — researching question {i}/{len(result.plan[:max_questions])}: {q}")
        notes = backend.chat(GATHER_PROMPT.format(question=q), model=model)
        if notes.error:
            result.error = f"gather failed on Q{i}: {notes.error}"
            return result
        result.notes.append({"question": q, "notes": notes.text})
    result.phases_run = 2

    # ── Phase 3: synthesize ──────────────────────────────────────────────────
    if progress:
        progress("Phase 3/3 — synthesizing the final report…")
    notes_block = "\n\n".join(
        f"### Q{i+1}: {item['question']}\n{item['notes']}"
        for i, item in enumerate(result.notes)
    )
    syn = backend.chat(SYNTHESIZE_PROMPT.format(notes=notes_block), model=model)
    if syn.error:
        result.error = f"synthesis failed: {syn.error}"
        return result
    result.report = syn.text
    # The full agent (agy) sometimes writes the report to an artifact file and
    # replies with a file:// link — load it so the CLI shows the real content.
    artifact = _find_artifact_file(syn.text)
    if artifact:
        try:
            content = Path(artifact).read_text(encoding="utf-8")
            if content.strip():
                result.report = content
        except OSError:
            pass
    result.phases_run = 3
    return result


_ARTIFACT_RE = re.compile(r"file://(/[^\s\])<>\"]+\.md)")


def _find_artifact_file(text: str):
    """Return the local .md path if the agent referenced an artifact file."""
    m = _ARTIFACT_RE.search(text or "")
    return m.group(1) if m else None


def _extract_questions(text: str) -> list[str]:
    """Pull '1. question' style lines out of the plan response."""
    questions = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^\d+[\.\)]\s*(.+)$", line)
        if m and len(m.group(1)) > 5:
            questions.append(m.group(1))
    return questions

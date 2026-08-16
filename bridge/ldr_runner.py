#!/usr/bin/env python3
"""ldr_runner.py — run the OFFICIAL Local Deep Research pipeline via its web API.

Drives the same server-side flow the LDR web UI uses (user's saved settings:
strategy, iterations, model) so forum research gets the full complex pipeline
(sub-questions, verification, multi-engine search) instead of a simplified
programmatic run.

Usage:
    /home/luigi/conda-envs/dr-ldr/bin/python ldr_runner.py "研究主題"
"""

import json
import os
import sys
import time

os.environ.setdefault("LDR_DATA_DIR", "/home/luigi/ldr-data")

from local_deep_research.api import LDRClient  # noqa: E402

topic = sys.argv[1] if len(sys.argv) > 1 else ""
if not topic.strip():
    print(json.dumps({"error": "empty topic"}), flush=True)
    sys.exit(1)

BASE_URL = os.environ.get("LDR_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.environ.get("LDR_USERNAME", "luigi")
PASSWORD = os.environ.get("LDR_PASSWORD", "k1731113")
TIMEOUT = int(os.environ.get("LDR_TIMEOUT", "7200"))

client = LDRClient(base_url=BASE_URL)
t0 = time.time()

if not client.login(USERNAME, PASSWORD):
    print(json.dumps({"error": "LDR login failed"}), flush=True)
    sys.exit(1)

# Start research the way the web UI does: no model/iterations overrides, so
# the server uses the user's saved settings (strategy, iterations, LLM).
response = client.session.post(
    f"{BASE_URL}/research/api/start",
    json={
        "query": topic,
        "search_engines": ["searxng"],
        "mode": "full",
        # Force the cheap/fast model for forum runs; the user's saved UI
        # default is deepseek-v4-pro (more expensive).
        "model": os.environ.get("LDR_MODEL", "deepseek-v4-flash"),
    },
    headers=client._api_headers(),
)
if response.status_code != 200:
    print(json.dumps({"error": f"start failed HTTP {response.status_code}: {response.text[:300]}"}), flush=True)
    sys.exit(1)

research_id = response.json().get("research_id")
if not research_id:
    print(json.dumps({"error": "no research_id returned"}), flush=True)
    sys.exit(1)

print(json.dumps({"research_id": research_id}), flush=True)

# Poll status; the full pipeline can take a long time.
try:
    result = client.wait_for_research(research_id, TIMEOUT)
except Exception as e:  # noqa: BLE001
    print(json.dumps({"error": str(e)[:500]}), flush=True)
    sys.exit(1)

# Prefer the saved report from the user DB (the HTTP report endpoint can be
# slow right after a run completes). Sources come from research_resources.
from local_deep_research.database.session_context import get_user_db_session  # noqa: E402
from local_deep_research.database.models import ResearchHistory, ResearchResource  # noqa: E402

report = ""
sources = []
try:
    with get_user_db_session(USERNAME, password=PASSWORD) as db:
        row = db.query(ResearchHistory).filter_by(id=research_id).first()
        if row and row.report_content:
            report = row.report_content
        for r in db.query(ResearchResource).filter_by(research_id=research_id).limit(30).all():
            if r.url:
                sources.append(
                    {
                        "title": r.title or r.url,
                        "url": r.url,
                        "snippet": (r.content_preview or "")[:200],
                    }
                )
except Exception as e:  # noqa: BLE001
    pass

if not report:
    summary = result.get("summary") or ""
    findings = result.get("findings") or ""
    if isinstance(findings, dict):
        findings = findings.get("summary") or findings.get("report") or ""
    report = f"{summary}\n\n{findings}".strip()
if not sources:
    sources = result.get("sources") or []

print(
    json.dumps(
        {
            "report": report or "（研究完成，但沒有產生報告內容。）",
            "sources": sources[:30],
            "duration_seconds": round(time.time() - t0, 1),
        },
        ensure_ascii=False,
    ),
    flush=True,
)

#!/usr/bin/env python3
"""ldr_runner.py — run the self-hosted Local Deep Research pipeline headlessly.

Called by the bridge (POST /v1/local-deep-research) as a subprocess using the
dr-ldr conda env. Uses the user's saved LDR settings (deepseek-v4-flash via
opencode.ai, local SearXNG) and prints a JSON report.

Usage:
    /home/luigi/conda-envs/dr-ldr/bin/python ldr_runner.py "研究主題"
"""

import json
import os
import sys
import time

os.environ.setdefault("LDR_DATA_DIR", "/home/luigi/ldr-data")
os.environ.setdefault("LDR_BOOTSTRAP_ALLOW_UNENCRYPTED", "true")

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING", format="{time:%H:%M:%S} {level} {message}")

from local_deep_research.api import detailed_research  # noqa: E402
from local_deep_research.api.settings_utils import create_settings_snapshot  # noqa: E402
from local_deep_research.database.session_context import get_user_db_session  # noqa: E402
from local_deep_research.web.services.settings_service import get_settings_manager  # noqa: E402

topic = sys.argv[1] if len(sys.argv) > 1 else ""

if not topic.strip():
    print(json.dumps({"error": "empty topic"}), flush=True)
    sys.exit(1)

# Load the user's saved LDR settings (LLM endpoint + keys), then pin the
# pipeline to the local SearXNG + the fast model for reliable forum runs.
with get_user_db_session("luigi", password="k1731113") as db:
    snap = get_settings_manager(db).get_all_settings()

snap["search.engine.web.searxng.default_params.instance_url"] = "http://127.0.0.1:8888"
# LDR defaults to language=en for SearXNG, which poisons Chinese queries with
# irrelevant results (YouTube/Google-Scholar junk). No language filter works
# best with this instance (same finding as the bridge chat grounding).
snap["search.engine.web.searxng.default_params.language"] = "all"
snap["search.iterations"] = int(os.environ.get("LDR_ITERATIONS", "3"))
snap["search.max_results"] = int(os.environ.get("LDR_MAX_RESULTS", "15"))

t0 = time.time()
try:
    result = detailed_research(
        query=topic,
        iterations=int(os.environ.get("LDR_ITERATIONS", "3")),
        search_tool="searxng",
        search_strategy="source_based",
        provider="openai_endpoint",
        model_name=os.environ.get("LDR_MODEL", "deepseek-v4-flash"),
        temperature=0.2,
        settings_snapshot=snap,
        programmatic_mode=True,
    )
    summary = result.get("summary") or ""
    report = result.get("report") or result.get("content") or ""
    sources = result.get("sources") or []
    print(
        json.dumps(
            {
                "report": (summary + "\n\n" + report).strip() or "（研究完成，但沒有產生報告內容。）",
                "sources": sources[:20],
                "duration_seconds": round(time.time() - t0, 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
except Exception as e:  # noqa: BLE001
    print(json.dumps({"error": str(e)[:500], "duration_seconds": round(time.time() - t0, 1)}), flush=True)
    sys.exit(1)

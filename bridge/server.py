#!/usr/bin/env python3
"""
bridge/server.py — HTTP bridge between Discourse and the Gemini AI Pro demo.

Endpoints (all require `Authorization: Bearer <BRIDGE_TOKEN>` if set):

    GET  /health                → {ok, backend, available, backend_info}
    POST /v1/chat               → {reply, model, usage, error}   (plugin /deep-style)
    POST /v1/chat/completions   → OpenAI-compatible (stream + non-stream, tools)
    POST /v1/deep-research      → {report, sources, plan, ...}

Backend: agy (live search) / direct (knowledge + tools) / mock. Choose with
--backend. The direct backend sends the Antigravity client headers, which the
internal API requires for tool/function-call requests.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import urllib.parse
import subprocess
import sys
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from gemini_backends import build_prompt, pick_backend, OpenCodeBackend  # noqa: E402
from deep_research import run_research  # noqa: E402

import re as _re

sys.path.insert(0, str(Path(__file__).resolve().parent))
import searxng  # noqa: E402

IMAGE_GEN_MODEL = "gemini-3.1-flash-image"
IMAGE_GEN_PATTERNS = [
    r"畫一張", r"畫個", r"生成圖片", r"生成一張", r"生成图像", r"繪製", r"繪畫", r"插圖",
    r"設計一張", r"製作圖片", r"圖片生成", r"畫一[張幅]", r"幫我畫", r"畫出",
    r"\bdraw\b", r"\bdraw me\b", r"generate an? image", r"create an? image",
    r"generate a picture", r"create a picture", r"image of a", r"picture of a",
    r"illustration", r"\blogo\b", r"icon of", r"meme",
]

def looks_like_image_request(messages) -> bool:
    """True when the CURRENT user turn asks to *generate* an image.

    Only the last user message is considered — earlier turns in the topic
    history must never trigger image routing for a new request.
    """
    has_image_input = False
    last_text = None
    for m in reversed(messages or []):
        c = m.get("content")
        if isinstance(c, str):
            if c.strip():
                last_text = c
            break
        if isinstance(c, list):
            for el in reversed(c):
                if isinstance(el, dict) and el.get("type") in ("image_url", "file"):
                    has_image_input = True
                if isinstance(el, dict) and el.get("type") == "text" and str(el.get("text", "")).strip():
                    last_text = str(el.get("text", ""))
                    break
            break
    if has_image_input:
        return False  # analysing an uploaded image, not generating
    if not last_text:
        return False
    return any(_re.search(p, last_text, _re.I) for p in IMAGE_GEN_PATTERNS)


FORUM_BASE = os.environ.get("FORUM_BASE_URL", "").rstrip("/")
FORUM_API_KEY = os.environ.get("FORUM_API_KEY", "")
FORUM_USER = os.environ.get("FORUM_BOT_USERNAME", "gemini")


def upload_to_forum(mime_type: str, b64data: str) -> str:
    """Upload a generated image to the Discourse forum; returns markdown or ""."""
    if not FORUM_BASE or not FORUM_API_KEY:
        return ""
    import base64 as _b64
    import tempfile, uuid, mimetypes
    ext = mimetypes.guess_extension(mime_type) or ".png"
    payload = _b64.b64decode(b64data)
    boundary = f"----gemini-bridge-{uuid.uuid4().hex}"
    fname = f"gemini_{uuid.uuid4().hex[:10]}{ext}"
    body = b""
    for name, value in (("type", "composer"), ("user_id", ""), ("synchronous", "true")):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"{fname}\"\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode()
    body += payload + b"\r\n" + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{FORUM_BASE}/uploads.json", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Api-Key", FORUM_API_KEY)
    req.add_header("Api-Username", FORUM_USER)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        url = data.get("url") or ""
        if url:
            # Always emit a relative path (/uploads/...) so the image renders
            # regardless of the host the viewer uses (LAN IP vs Tailscale).
            url = urllib.parse.urlparse(url).path or url
            return f"![generated image]({url})"
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[bridge] upload failed: {e}\n")
    return ""


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "GeminiBridge/0.3"
    backend = None
    opencode_backend = None
    token = ""

    OPENCODE_KEY_FILE = str(Path(__file__).resolve().parent / ".opencode_key")

    @classmethod
    def _load_opencode_key_file(cls) -> str:
        """Admin-set OpenCode key persisted by the plugin (overrides env)."""
        try:
            with open(cls.OPENCODE_KEY_FILE, encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
        except OSError:
            pass
        return os.environ.get("OPENCODE_API_KEY", "")

    @classmethod
    def _save_opencode_key_file(cls, api_key: str) -> None:
        try:
            Path(cls.OPENCODE_KEY_FILE).write_text(api_key.strip(), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def _refresh_opencode_backend(cls) -> None:
        """Create/repoint the OpenCode backend with the current key."""
        key = cls._load_opencode_key_file()
        if cls.opencode_backend is not None:
            cls.opencode_backend.api_key = key
        else:
            cls.opencode_backend = OpenCodeBackend(api_key=key)

    OPENCODE_PREFIXES = ("deepseek", "mimo", "glm", "grok", "kimi", "minimax", "qwen", "gpt-5.6", "hy3")

    def backend_for(self, model):
        """Route models that live on the OpenCode Zen Go endpoint."""
        m = (model or "").lower()
        if m.startswith(self.OPENCODE_PREFIXES):
            if BridgeHandler.opencode_backend is None:
                BridgeHandler._refresh_opencode_backend()
            return BridgeHandler.opencode_backend
        return self.backend

    # ── helpers ─────────────────────────────────────────────────────────────
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n)) if n else {}
        except (ValueError, json.JSONDecodeError):
            return None

    def _authed(self) -> bool:
        if not self.token:
            return True
        return self.headers.get("Authorization") == f"Bearer {self.token}"

    def _error(self, code: int, message: str, err_type: str = "api_error") -> None:
        self._send(code, {"error": {"message": message, "type": err_type, "param": None, "code": None}})

    # ── routes ──────────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/health":
            b = self.backend
            self._send(200, {
                "ok": True,
                "backend": b.name if hasattr(b, "name") else "?",
                "available": bool(getattr(b, "available", lambda: False)()),
                "backend_info": getattr(b, "describe", lambda: "")()[:120],
                "time": time.time(),
            })
        elif self.path == "/api/quota":
            try:
                gem = self.backend.quota()
                BridgeHandler._refresh_opencode_backend()
                oc = BridgeHandler.opencode_backend.quota()
                merged = dict(gem)  # keep top-level models + fetched_at (Discourse plugin reads @quota["models"])
                merged["opencode"] = oc
                self._send(200, merged)
            except Exception as e:  # noqa: BLE001
                self._send(502, {"error": str(e)})
        elif self.path == "/quota" or self.path.startswith("/quota?"):
            try:
                gem = self.backend.quota()
                if BridgeHandler.opencode_backend is None:
                    BridgeHandler.opencode_backend = OpenCodeBackend()
                oc = BridgeHandler.opencode_backend.quota()
            except Exception as e:  # noqa: BLE001
                body = (f"<!doctype html><html><body style='font-family:sans-serif;padding:2em'>"
                        f"<h2>⚠️ 無法讀取配額</h2><p>{e}</p><meta http-equiv='refresh' content='60'>"
                        f"</body></html>").encode()
                self._send_html(body)
                return
            self._send_html(self._quota_page(gem, oc))
        else:
            self._send(404, {"error": "not found"})

    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _quota_page(gem_data: dict, oc_data: dict | None = None) -> bytes:
        import html as _html
        rows = []
        for m in gem_data.get("models", []):
            frac = float(m.get("remaining", 0))
            pct = frac * 100
            if frac > 0.5:
                color = "#16a34a"
            elif frac > 0.1:
                color = "#ca8a04"
            else:
                color = "#dc2626"
            rows.append(
                "<tr>"
                f"<td>{_html.escape(m.get('name', ''))}<div class='key'>{_html.escape(m.get('key', ''))}</div></td>"
                "<td class='bar-cell'><div class='bar'><div class='fill' "
                f"style='width:{pct:.1f}%;background:{color}'></div></div>"
                f"<span class='pct'>{pct:.2f}%</span></td>"
                f"<td class='reset' data-reset='{_html.escape(m.get('reset_time', ''))}'>—</td>"
                "</tr>"
            )
        oc_rows = ""
        if oc_data is not None:
            for m in oc_data.get("models", []):
                frac = float(m.get("remaining", 0))
                pct = frac * 100
                if frac > 0.5:
                    color = "#16a34a"
                elif frac > 0.1:
                    color = "#ca8a04"
                else:
                    color = "#dc2626"
                oc_rows += (
                    "<tr>"
                    f"<td>{_html.escape(m.get('name', ''))}<div class='key'>{_html.escape(m.get('key', ''))}</div></td>"
                    "<td class='bar-cell'><div class='bar'><div class='fill' "
                    f"style='width:{pct:.1f}%;background:{color}'></div></div>"
                    f"<span class='pct'>{pct:.2f}%</span></td>"
                    f"<td class='reset' data-reset='{_html.escape(m.get('reset_time', ''))}'>—</td>"
                    "</tr>"
                )
            if oc_data.get("error"):
                oc_rows += f"<tr><td colspan=3 class='key warn'>⚠️ {_html.escape(oc_data['error'])}</td></tr>"
        fetched = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gem_data.get("fetched_at", time.time())))
        gem_rows = "\n".join(rows)
        source = "fetchAvailableModels/quotaInfo" if not oc_data else "fetchAvailableModels/quotaInfo + OpenCode /usage"
        html = f"""<!doctype html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>配額監視 (Antigravity + OpenCode)</title>
<style>
 body {{ font-family: -apple-system, 'Segoe UI', 'Noto Sans TC', sans-serif; margin: 0;
        background: #0f172a; color: #e2e8f0; }}
 .wrap {{ max-width: 860px; margin: 0 auto; padding: 24px 16px; }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }}
 .sub {{ color: #94a3b8; font-size: 13px; margin-bottom: 20px; }}
 h2 {{ font-size: 15px; margin: 28px 0 8px; color: #7dd3fc; }}
 table {{ width: 100%; border-collapse: collapse; }}
 th {{ text-align: left; font-size: 12px; color: #94a3b8; padding: 8px;
      border-bottom: 1px solid #1e293b; }}
 td {{ padding: 10px 8px; border-bottom: 1px solid #1e293b; font-size: 14px; }}
 .key {{ color: #64748b; font-size: 11px; font-family: monospace; }}
 .bar {{ background: #1e293b; border-radius: 6px; height: 10px; min-width: 180px; overflow: hidden; }}
 .fill {{ height: 100%; border-radius: 6px; }}
 .pct {{ font-variant-numeric: tabular-nums; margin-left: 8px; font-size: 13px; }}
 .reset {{ color: #94a3b8; font-variant-numeric: tabular-nums; font-size: 13px; }}
 .warn {{ color: #fca5a5; }}
</style></head><body><div class="wrap">
<h1>🧪 配額監視</h1>
<div class="sub">每 60 秒自動更新 · 資料擷取於 {fetched}（本機時間）· 來源：{source}</div>
<h2>Antigravity（Gemini 模型）</h2>
<table><thead><tr><th>模型</th><th>剩餘額度</th><th>重設倒數</th></tr></thead><tbody>
{gem_rows}
</tbody></table>
<h2>OpenCode Go（deepseek-v4-flash / mimo-v2.5）</h2>
<table><thead><tr><th>模型</th><th>剩餘額度</th><th>重設倒數</th></tr></thead><tbody>
{oc_rows if oc_rows else "<tr><td colspan=3 class='key'>—</td></tr>"}
</tbody></table>
<script>
function tick() {{
  document.querySelectorAll('td.reset').forEach(el => {{
    const iso = el.dataset.reset;
    if (!iso) {{ el.textContent = '—'; return; }}
    const t = new Date(iso).getTime() - Date.now();
    if (t <= 0) {{ el.textContent = '已重設 ✓'; el.classList.add('warn'); return; }}
    const s = Math.floor(t / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    el.textContent = h + 'h ' + m + 'm ' + sec + 's';
    if (s < 900) el.classList.add('warn');
  }});
}}
tick(); setInterval(tick, 1000);
</script></body></html>"""
        return html.encode()

    def do_POST(self):
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        body = self._read_json()
        if body is None:
            self._send(400, {"error": "invalid JSON"})
            return

        if self.path == "/v1/chat/completions":
            self._chat_completions(body)
        elif self.path == "/v1/local-deep-research":
            self._local_deep_research(body)
        elif self.path == "/v1/chat":
            self._chat(body)
        elif self.path == "/v1/deep-research":
            self._deep_research(body)
        elif self.path == "/v1/config/opencode-key":
            key = (body.get("api_key") or "").strip()
            if not key:
                self._error(400, "api_key required", "invalid_request_error")
                return
            BridgeHandler._save_opencode_key_file(key)
            BridgeHandler._refresh_opencode_backend()
            sys.stderr.write(f"[bridge] OpenCode API key updated by admin ({key[:8]}...)\n")
            self._send(200, {"ok": True, "updated": True})
        else:
            self._send(404, {"error": "not found"})




    def _grounded_answer(self, messages, model):
        """Multi-round tool loop for the main backend: execute googleSearch
        tool calls with SearXNG and feed the results back (max 3 rounds)."""
        msgs = list(messages or [])
        result = None
        for _ in range(3):
            result = self.backend.chat_messages(msgs, model=model, tools=None, tool_choice=None)
            if result.error:
                return result
            calls = result.tool_calls or []
            if not calls:
                return result
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                query = args.get("query") or args.get("q") or ""
                if name == "googleSearch" and query:
                    res = searxng.search(query)
                    content = (
                        "搜尋結果：\n"
                        + "\n".join(f"[{i}] {r['title']} — {r['url']}\n    {r['content']}"
                                     for i, r in enumerate(res, 1))
                        or "（無結果）"
                    )
                else:
                    content = f"unknown tool: {name}"
                msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
                msgs.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": content})
        return result

    def _smol_answer_stream(self, messages, model):
        """Run the smolagents CodeAgent (SearXNG tool) as a subprocess and
        stream its answer back in chunks. Used for OpenCode Go models.

        The agent gets the LAST user turn as its task PLUS the prior thread
        history as context (JSON in argv[3]) so it can answer questions like
        "summarize the previous post" instead of replying in confusion.
        """
        question = searxng.last_user_query(messages or [])
        py = os.environ.get("SMOLAGENTS_PY", "/home/luigi/bridge-venv/bin/python")
        script = Path(__file__).resolve().parent / "smol_agent.py"
        if not question or not Path(py).exists() or not script.exists():
            yield "\n\n[agent unavailable — answer from model knowledge]"
            return
        # prior history = all messages except the last user turn (the live question)
        prior = list(messages or [])
        for m in reversed(prior):
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                prior.pop()
                break
            if isinstance(c, list) and any(isinstance(el, dict) and el.get("type") == "text" for el in c):
                prior.pop()
                break
        history_json = json.dumps(prior, ensure_ascii=False) if prior else ""
        try:
            proc = subprocess.run(
                [py, str(script), question, "code", history_json],
                capture_output=True,
                text=True,
                timeout=300,
                env=os.environ.copy(),
            )
            out = (proc.stdout or "").strip()
            out = "\n".join(l for l in out.splitlines() if l.strip() != "Reached max steps.").strip()
            if proc.returncode != 0 or not out:
                err = (proc.stderr or "").strip().splitlines()
                out = "\n\n[agent error: " + (err[-1][:300] if err else "unknown") + "]"
        except Exception as e:  # noqa: BLE001
            out = f"\n\n[agent error: {e}]"
        for i in range(0, len(out), 100):
            yield out[i : i + 100]

    def _search_query_for(self, messages, model):
        """Ask the routed model to turn the pending question into search-engine
        keywords (one cheap round trip), so SearXNG gets keyword-style queries
        instead of full sentences/questions."""
        question = searxng.last_user_query(messages or [])
        if not question or len(question) < 4:
            return ""
        try:
            backend = self.backend_for(model)
            result = backend.chat_messages(
                [
                    {
                        "role": "user",
                        "content": (
                            "把以下問題轉成適合搜尋引擎的關鍵字查詢（5～15字，只輸出關鍵字本身，"
                            f"不要標點、不要引號、不要問句）：{question}"
                        ),
                    }
                ],
                model=model,
            )
            kw = (result.text or "").strip().strip('"').strip("'").strip()
            return kw[:40] if kw else ""
        except Exception:
            return ""

    def _chat(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.5-flash"
        if not messages:
            self._send(400, {"error": "messages required"})
            return
        prompt = build_prompt(messages)
        t0 = time.time()
        result = self.backend_for(model).chat(prompt, model=model)
        self._send(200, {
            "reply": result.text,
            "model": result.model or model,
            "usage": result.usage,
            "error": result.error or None,
            "duration_seconds": round(time.time() - t0, 1),
        })

    # ── OpenAI-compatible chat completions ──────────────────────────────────
    def _chat_completions(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.5-flash"
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        # image-generation intent on a base Gemini chat model -> route to the
        # image model. Non-Gemini models (opencode etc.) never get rerouted.
        if (
            tools is None
            and looks_like_image_request(messages)
            and model != IMAGE_GEN_MODEL
            and (model or "").lower().startswith("gemini-")
        ):
            sys.stderr.write(f"[bridge] routing image request: {model} -> {IMAGE_GEN_MODEL}\n")
            model = IMAGE_GEN_MODEL
            # append quality guidance to the last user text (preview model needs it)
            for m in reversed(messages):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    m["content"] = (c.rstrip() +
                        "\n\n（請輸出高品質、明亮、色彩鮮豔、構圖清晰的圖片，並嚴格遵循使用者指定的顏色與細節。）")
                    break
                if isinstance(c, list):
                    for el in reversed(c):
                        if isinstance(el, dict) and el.get("type") == "text":
                            el["text"] = (str(el.get("text", "")).rstrip() +
                                "\n\n（請輸出高品質、明亮、色彩鮮豔、構圖清晰的圖片，並嚴格遵循使用者指定的顏色與細節。）")
                            break
                    if isinstance(c[-1], dict) and c[-1].get("type") == "text":
                        break
        if not messages:
            self._error(400, "messages required", "invalid_request_error")
            return

        # web grounding:
        #   - OpenCode Go models  -> smolagents CodeAgent (SearXNG tool)
        #   - other non-Gemini     -> static SearXNG injection (as before)
        #   - Gemini models        -> native googleSearch grounding (no-op here)
        agent_grounded = self.backend_for(model).name == "opencode"
        web_results = []
        if not agent_grounded:
            search_query = self._search_query_for(messages, model)
            messages, web_results = searxng.inject(messages, model, search_query=search_query)

        if body.get("stream"):
            self._stream_completions(
                messages, model, tools=tools, tool_choice=tool_choice, web_results=web_results
            )
            return

        try:
            if self.backend_for(model).name == "opencode":
                from agent import web_answer
                result = self.backend_for(model).chat_messages(messages, model=model)
                if not result.error:
                    result.text = web_answer(self.backend_for(model), messages, model)
            else:
                result = self._grounded_answer(messages, model)
        except Exception as e:  # noqa: BLE001
            self._error(500, str(e), "server_error")
            return
        if result.error:
            self._error(502, result.error, "upstream_error")
            return

        if web_results:
            result.text = result.text.rstrip() + searxng.sources_markdown(web_results)

        usage = result.usage or {}
        message = {"role": "assistant", "content": result.text or None}
        if result.tool_calls:
            message["tool_calls"] = result.tool_calls
        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": result.model or model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason or ("tool_calls" if result.tool_calls else "stop"),
            }],
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        }
        self._send(200, resp)

    def _stream_completions(self, messages, model, tools=None, tool_choice=None, web_results=None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        # no Content-Length, no keep-alive -> connection closes at end of stream,
        # so HTTP clients (Net::HTTP etc.) see EOF and stop reading.
        self.close_connection = True
        self.end_headers()

        def chunk(delta_text=None, finish=None, role_done=False):
            delta = {}
            if role_done:
                delta["role"] = "assistant"
            if delta_text:
                delta["content"] = delta_text
            d = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()

        _tool_idx = {"n": 0}

        def tool_chunk(call):
            idx = _tool_idx["n"]
            _tool_idx["n"] += 1
            delta = {"tool_calls": [{
                "index": idx,
                "id": call["id"],
                "type": "function",
                "function": {"name": call["function"]["name"], "arguments": call["function"]["arguments"]},
            }]}
            d = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()

        _images = []
        _buf = []  # final-attempt (text|tool) chunks, emitted once (no duplicates)
        if self.backend_for(model).name == "opencode":
            try:
                chunk(role_done=True)
                for delta in self._smol_answer_stream(messages, model):
                    chunk(delta_text=delta)
                chunk(finish="stop")
            except Exception as e:  # noqa: BLE001
                try:
                    chunk(delta_text=f"\n\n[bridge error: {e}]", finish="stop")
                except Exception:
                    pass
            return
        image_requested = (model == IMAGE_GEN_MODEL) or (
            (model or "").lower().startswith("gemini-") and looks_like_image_request(messages)
        )
        attempts = 2 if image_requested else 1
        attempt_exc = None
        try:
            chunk(role_done=True)
            for attempt in range(attempts):
                attempt_buf = []
                attempt_imgs = []
                got_error = False
                try:
                    for delta, meta in self.backend_for(model).chat_messages_stream(messages, model=model, tools=tools, tool_choice=tool_choice):
                        if delta:
                            if isinstance(delta, str) and "[bridge error:" in delta:
                                got_error = True
                            attempt_buf.append(("text", delta))
                        if meta and meta.get("type") == "tool_call":
                            attempt_buf.append(("tool", meta["tool_call"]))
                        if meta and meta.get("type") == "image":
                            attempt_imgs.append(meta)
                except Exception as e:  # noqa: BLE001 — read timeout etc.: retry next attempt
                    attempt_exc = e
                else:
                    attempt_exc = None
                    _buf = attempt_buf
                    _images = attempt_imgs
                    if not image_requested or _images or got_error:
                        break
                if attempt + 1 < attempts:
                    time.sleep(2)
            if attempt_exc is not None and not _buf and not _images:
                raise attempt_exc
            for kind, payload in _buf:
                if kind == "text" and payload:
                    chunk(delta_text=payload)
                elif kind == "tool":
                    tool_chunk(payload)
            upload_failures = 0
            for img in _images:
                md = upload_to_forum(img.get("mimeType", "image/png"), img.get("data", ""))
                if md:
                    chunk(delta_text="\n\n" + md)
                else:
                    upload_failures += 1
            if image_requested and not _images:
                chunk(delta_text="\n\n⚠️ 上游影像模型未回傳圖片（可能暫時過載或安全過濾），已重試一次仍失敗，請稍後再試。")
            elif image_requested and upload_failures:
                chunk(delta_text="\n\n⚠️ 圖片已生成但上傳至論壇失敗，請稍後重試。")
            if web_results:
                chunk(delta_text=searxng.sources_markdown(web_results))
            chunk(finish="stop")
        except Exception as e:  # noqa: BLE001
            try:
                chunk(delta_text=f"\n\n[bridge error: {e}]", finish="stop")
            except Exception:
                pass
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            pass


    def _local_deep_research(self, body: dict) -> None:
        topic = (body.get("topic") or "").strip()
        if not topic:
            self._send(400, {"error": "topic required"})
            return
        py = os.environ.get("LDR_PY", "/home/luigi/conda-envs/dr-ldr/bin/python")
        script = str(Path(__file__).resolve().parent / "ldr_runner.py")
        t0 = time.time()
        try:
            proc = subprocess.run(
                [py, script, topic],
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("LDR_TIMEOUT", "1800")),
                env=os.environ.copy(),
            )
            out = (proc.stdout or "").strip()
            if proc.returncode != 0 or not out:
                err = ((proc.stderr or "").strip().splitlines() or ["unknown error"])[-1]
                self._send(502, {"error": f"LDR failed: {err[:400]}"})
                return
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                self._send(502, {"error": f"LDR non-JSON output: {out[:200]}"})
                return
            data["duration_seconds"] = round(time.time() - t0, 1)
            self._send(200, data)
        except Exception as e:  # noqa: BLE001
            self._send(502, {"error": str(e)[:400]})

    def _deep_research(self, body: dict) -> None:
        topic = body.get("topic") or ""
        if not topic:
            self._send(400, {"error": "topic required"})
            return
        t0 = time.time()
        result = run_research(
            self.backend,
            topic,
            max_questions=int(body.get("max_questions", 3)),
            model=body.get("model") or "gemini-3.5-flash",
        )
        if result.error:
            self._send(502, {"error": result.error})
            return
        self._send(200, {
            "report": result.report,
            "sources": result.sources[:50],
            "plan": result.plan,
            "duration_seconds": round(time.time() - t0, 1),
            "backend": getattr(self.backend, "name", "?"),
        })

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[bridge] %s %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini bridge for Discourse")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--backend", choices=["agy", "antigravity-app", "direct", "gemini-api", "gemini", "mock"],
                    default=os.environ.get("BACKEND"))
    ap.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""))
    args = ap.parse_args()

    BridgeHandler.backend = pick_backend(args.backend)
    BridgeHandler.token = args.token

    sys.stderr.write(f"[bridge] backend: {getattr(BridgeHandler.backend, 'describe', lambda: '')()}\n")
    sys.stderr.write(f"[bridge] auth: {'token required' if args.token else 'open'}\n")
    httpd = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    sys.stderr.write(f"[bridge] listening on http://{args.host}:{args.port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

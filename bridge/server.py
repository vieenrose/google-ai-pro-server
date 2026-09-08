#!/usr/bin/env python3
"""
bridge/server.py — Sloth AI bridge: Google AI Pro (Antigravity) only.

Sloth AI plugin now does ONE thing: connect Discourse to the Google AI Pro
subscription via the Antigravity application backend. Google's native
googleSearch grounding is built into the backend, so no SearXNG / smolagents
needed. Models (gemini / claude) are configured in Discourse AI pointing at
this bridge (open_ai provider).

Endpoints (all require `Authorization: Bearer <BRIDGE_TOKEN>` if set):

    GET  /health                       → {ok, backend, available}
    POST /v1/chat                      → {reply, model, usage, error}
    POST /v1/chat/completions          → OpenAI-compatible (stream + non-stream)
    POST /v1/deep-research             → {report, sources, plan}
    GET  /api/quota                    → Antigravity per-model quota
    GET  /api/models                   → usable Antigravity models
    GET  /v1/config/antigravity-auth           → auth/subscription status
    POST /v1/config/antigravity-auth/url      → PKCE sign-in URL
    POST /v1/config/antigravity-auth/exchange → save fresh token
    GET  /quota | /sloth-ai            → HTML quota/status page
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import urllib.parse
import sys
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from gemini_backends import (  # noqa: E402
    build_prompt, pick_backend, ChatResult,
    AntigravityAppBackend, OpenCodeBackend, TogetherAIBackend,
)
from deep_research import run_research  # noqa: E402

IMAGE_GEN_MODEL = "gemini-3.1-flash-image"
import re as _re
IMAGE_GEN_PATTERNS = [
    _re.compile(p) for p in [
        r"畫一張", r"畫個", r"生成圖片", r"生成一張", r"生成图像", r"繪製", r"繪畫", r"插圖",
        r"設計一張", r"製作圖片", r"圖片生成", r"畫一[張幅]", r"幫我畫", r"畫出",
        r"\bdraw\b", r"\bdraw me\b", r"generate an? image", r"create an? image",
        r"generate a picture", r"create a picture", r"image of a", r"picture of a",
        r"illustration", r"\blogo\b", r"icon of", r"meme",
    ]
]


def looks_like_image_request(messages) -> bool:
    """True when the CURRENT user turn asks to *generate* an image."""
    has_image_input = False
    last_text = None
    for m in reversed(messages or []):
        c = m.get("content")
        if isinstance(c, list):
            for el in c:
                if isinstance(el, dict) and el.get("type") == "image":
                    has_image_input = True
        elif isinstance(c, str) and c.strip():
            last_text = c
            break
    return (last_text and any(p.search(last_text) for p in IMAGE_GEN_PATTERNS)) or has_image_input


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "SlothAI/0.4"
    backend = None
    token = ""

    # ── helpers ─────────────────────────────────────────────────────────────
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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

    # ── GET ────────────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/health":
            b = self.backend
            self._send(200, {
                "ok": True,
                "backend": b.name if hasattr(b, "name") else "?",
                "available": bool(getattr(b, "available", lambda: False)()),
                "backend_info": getattr(b, "describe", lambda: "")()[:140],
                "time": time.time(),
            })
        elif self.path == "/api/quota":
            try:
                self._send(200, self.backend.quota())
            except Exception as e:  # noqa: BLE001
                self._send(502, {"error": str(e)})
        elif self.path == "/api/models":
            try:
                gem = self.backend.quota()
                ag_models = []
                for m in (gem.get("models") or []):
                    key = m.get("key")
                    if not key or str(key).startswith(("chat_", "tab_")):
                        continue
                    # Drop deprecated / too-old models from the enable list.
                    if key.startswith("gemini-2"):
                        continue
                    ag_models.append({"id": key, "name": key})
                self._send(200, {"antigravity": ag_models})
            except Exception as e:  # noqa: BLE001
                self._send(502, {"error": str(e)})
        elif self.path == "/v1/config/antigravity-auth":
            try:
                import agy_auth  # noqa: E402
                self._send(200, agy_auth.status())
            except Exception as e:  # noqa: BLE001
                self._send(502, {"error": str(e)})
        elif self.path == "/quota" or self.path.startswith("/quota?") or self.path == "/sloth-ai":
            self._send_html(self._quota_page(self._quota_snapshot()))
        else:
            self._send(404, {"error": "not found"})

    # ── POST ───────────────────────────────────────────────────────────────
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
        elif self.path == "/v1/chat":
            self._chat(body)
        elif self.path == "/v1/deep-research":
            self._deep_research(body)
        elif self.path == "/v1/config/antigravity-auth/url":
            import agy_auth  # noqa: E402
            self._send(200, agy_auth.start_reauth())
        elif self.path == "/v1/config/antigravity-auth/exchange":
            import agy_auth  # noqa: E402
            code = (body.get("code") or "").strip()
            verifier = (body.get("verifier") or "").strip() or None
            if not code:
                self._error(400, "code required", "invalid_request_error")
                return
            self._send(200, agy_auth.complete_reauth(code, verifier))
        else:
            self._send(404, {"error": "not found"})

    # ── simple chat (plugin /deep-style) ──────────────────────────────────
    def _chat(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.7-flash-tiered"
        prompt = build_prompt(messages)
        t0 = time.time()
        result = self.backend.chat(prompt, model=model)
        self._send(200, {
            "reply": result.text,
            "model": result.model or model,
            "usage": result.usage,
            "error": result.error or None,
            "duration_seconds": round(time.time() - t0, 1),
        })

    def _deep_research_request(self, messages, model) -> str | None:
        """Return a research topic if the user message to a Gemini bot asks for
        deep research (so the chat bot itself can run it — no separate
        @deep-research agent needed). Matches:
          "deep research: <topic>" / "deep-research <topic>"
          "深入研究：<topic>" / "深入研究 <topic>"
          "/deep <topic>"
        """
        if not (model or "").lower().startswith("gemini-"):
            return None
        last = ""
        for m in reversed(messages or []):
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                last = c.strip()
                break
            if isinstance(c, list):
                texts = [e.get("text", "") for e in c if isinstance(e, dict) and e.get("type") == "text"]
                if texts:
                    last = " ".join(texts).strip()
                    break
        if not last:
            return None
        for pat in (
            r"deep[ -]research\s*[:：]?\s+(.+)$",
            r"深入研究\s*[:：]?\s+(.+)$",
            r"/deep\s+(.+)$",
            r"\./deep\s+(.+)$",
        ):
            m = _re.search(pat, last, _re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    def _run_chat_research(self, topic: str, model: str):
        """Run the Google AI Pro deep-research workflow and return a ChatResult
        whose text is the full report."""
        t0 = time.time()

        def _prog(msg: str):
            sys.stderr.write(f"[deep-research] {msg}\n")

        result = run_research(self.backend, topic, max_questions=3, model=model, progress=_prog)
        if result.error:
            return ChatResult(text="", error=result.error)
        usage = {}
        text = f"# 🔬 Google Deep Research: {topic}\n\n{result.report}"
        if result.sources:
            text += "\n\n## 📚 Sources\n" + "\n".join(f"- {s}" for s in result.sources[:40]) + "\n"
        return ChatResult(
            text=text,
            model=model,
            usage=usage,
            finish_reason="stop",
        )

    # ── OpenAI-compatible chat completions (Discourse AI + Sloth bots) ────
    def _chat_completions(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.7-flash-tiered"
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        if (
            tools is None
            and looks_like_image_request(messages)
            and model != IMAGE_GEN_MODEL
            and (model or "").lower().startswith("gemini-")
        ):
            sys.stderr.write(f"[bridge] routing image request: {model} -> {IMAGE_GEN_MODEL}\n")
            model = IMAGE_GEN_MODEL
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

        # Deep-research command on a Gemini bot → run the research workflow.
        topic = self._deep_research_request(messages, model)
        if topic:
            dr = self._run_chat_research(topic, model)
            if body.get("stream"):
                self._stream_research(dr)
            elif dr.error:
                self._error(502, dr.error, "upstream_error")
            elif dr.text:
                self._send(200, {
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": dr.text}, "finish_reason": "stop"}],
                    "usage": {},
                })
            return

        # Google AI Pro grounds natively via Antigravity's googleSearch tool.
        if body.get("stream"):
            self._stream_completions(messages, model, tools=tools, tool_choice=tool_choice)
            return

        try:
            result = self._grounded_loop(messages, model, tools=tools, tool_choice=tool_choice)
        except Exception as e:  # noqa: BLE001
            self._error(500, str(e), "server_error")
            return
        if result.error:
            self._error(502, result.error, "upstream_error")
            return

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
                "prompt_tokens": (result.usage or {}).get("promptTokenCount", 0),
                "completion_tokens": (result.usage or {}).get("candidatesTokenCount", 0),
                "total_tokens": (result.usage or {}).get("totalTokenCount", 0),
            },
        }
        self._send(200, resp)

    def _grounded_loop(self, messages, model, tools=None, tool_choice=None, max_rounds: int = 3):
        """Multi-round loop on the native Antigravity backend. Google's own
        googleSearch tool executes server-side; any tool call the model emits
        that the bridge must fulfil is handled here (none in the native path —
        kept for safety against non-native tool requests)."""
        msgs = list(messages or [])
        result = None
        for _ in range(max_rounds):
            result = self.backend.chat_messages(msgs, model=model, tools=tools, tool_choice=tool_choice)
            if result.error or not result.tool_calls:
                return result
            for call in result.tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name != "googleSearch":
                    return result  # non-native tool: hand back as-is
            # Native googleSearch is executed by Antigravity; a tool_call with
            # that name at this point means the backend expects a response.
            content = "（由 Google 原生 googleSearch 處理）"
            msgs.append({"role": "assistant", "content": None, "tool_calls": [call for call in result.tool_calls]})
            msgs.append({"role": "tool", "tool_call_id": result.tool_calls[0].get("id", ""), "content": content})
        return result

    # ── streaming (used by Discourse AI + Sloth bot chat/instant) ─────────
    def _stream_completions(self, messages, model, tools=None, tool_choice=None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.close_connection = True
        self.end_headers()

        def chunk(delta_text=None, finish=None, role_done=False, tool_call=None):
            delta = {}
            if role_done:
                delta["role"] = "assistant"
            if delta_text:
                delta["content"] = delta_text
            if tool_call:
                delta["tool_calls"] = [{
                    "index": 0, "id": tool_call.get("id", ""), "type": "function",
                    "function": {"name": tool_call.get("function", {}).get("name", ""), "arguments": tool_call.get("function", {}).get("arguments", "")},
                }]
            d = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()

        tool_calls_collected = []
        try:
            chunk(role_done=True)
            got_text = False
            for delta, meta in self.backend.chat_messages_stream(messages, model=model, tools=tools, tool_choice=tool_choice):
                if isinstance(delta, str) and delta:
                    chunk(delta_text=delta)
                    got_text = True
                if meta:
                    if meta.get("type") == "image":
                        md = self._upload_image(meta.get("mimeType", "image/png"), meta.get("data", ""))
                        if md:
                            chunk(delta_text="\n\n" + md)
                    elif meta.get("type") == "tool_call":
                        tool_calls_collected.append(meta["tool_call"])
                        chunk(tool_call=meta["tool_call"])
            if not got_text and not tool_calls_collected:
                chunk(delta_text="⚠️ 模型沒有回覆內容。", finish="stop")
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

    def _stream_research(self, result) -> None:
        """Stream a finished deep-research report as SSE chunks."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.close_connection = True
        self.end_headers()

        def chunk(text=None, finish=None, role_done=False):
            delta = {}
            if role_done:
                delta["role"] = "assistant"
            if text is not None:
                delta["content"] = text
            d = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": result.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
            self.wfile.flush()

        try:
            chunk(role_done=True)
            text = result.text or ""
            for i in range(0, len(text), 100):
                chunk(text=text[i : i + 100])
            chunk(finish="stop")
        except Exception:
            pass
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            pass

    # ── deep research (Google AI Pro, via Antigravity backend) ────────────
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
            model=body.get("model") or "gemini-3.6-flash",
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

    # ── image upload to forum ──────────────────────────────────────────────
    def _upload_image(self, mime_type: str, data_b64: str) -> str:
        """Upload a generated image to the Discourse forum via multipart form-data.
        Returns markdown or ""."""
        try:
            base = os.environ.get("FORUM_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
            key = os.environ.get("FORUM_API_KEY", "")
            user = os.environ.get("FORUM_BOT_USERNAME", "system")
            import base64 as _b64
            import mimetypes
            payload = _b64.b64decode(data_b64)
            boundary = f"----sloth-bridge-{uuid.uuid4().hex}"
            ext = mimetypes.guess_extension(mime_type) or ".png"
            fname = f"sloth_{uuid.uuid4().hex[:10]}{ext}"
            body = b""
            for name, value in (("type", "composer"), ("user_id", ""), ("synchronous", "true")):
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
            body += (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode()
            body += payload + b"\r\n" + f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(f"{base}/uploads.json", data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Api-Key", key)
            req.add_header("Api-Username", user)
            with urllib.request.urlopen(req, timeout=60) as resp:
                d = json.loads(resp.read().decode())
            url = d.get("url") or ""
            if url:
                # relative path so image renders regardless of viewer host
                url = urllib.parse.urlparse(url).path or url
                return f"![generated image]({url})"
            return ""
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[bridge] upload_to_forum failed: {e}\n")
            return ""

    # ── quota monitor snapshot (multi-backend) ─────────────────────────────
    def _quota_snapshot(self) -> dict:
        """Collect quota/state from every backend the bridge knows.

        Slow or unconfigured backends degrade to an error state instead of
        blocking the page: each fetch runs in its own thread with a timeout.
        """
        import concurrent.futures as _cf
        snap: dict = {"fetched_at": time.time()}

        def _ag() -> dict:
            try:
                q = self.backend.quota()
                return {"ok": True, "fetched_at": q.get("fetched_at"), "models": q.get("models") or []}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)[:200]}

        def _oc() -> dict:
            try:
                b = OpenCodeBackend()
                if not b.available():
                    return {"ok": False, "error": "未設定 (no API key)"}
                q = b.quota()
                if q.get("error"):
                    return {"ok": False, "error": str(q["error"])[:200]}
                return {"ok": True, "fetched_at": q.get("fetched_at"), "models": q.get("models") or []}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)[:200]}

        def _to() -> dict:
            try:
                b = TogetherAIBackend()
                if not b.available():
                    return {"ok": False, "error": "未設定 (no API key)"}
                return {"ok": True, "models": b.list_models()}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)[:200]}

        ex = _cf.ThreadPoolExecutor(max_workers=3)
        try:
            f_ag, f_oc, f_to = ex.submit(_ag), ex.submit(_oc), ex.submit(_to)
            try:
                snap["antigravity"] = f_ag.result(timeout=25)
            except Exception:
                snap["antigravity"] = {"ok": False, "error": "讀取逾時 (timeout)"}
            try:
                snap["opencode"] = f_oc.result(timeout=8)
            except Exception:
                snap["opencode"] = {"ok": False, "error": "無法連線 (timeout)"}
            try:
                snap["together"] = f_to.result(timeout=8)
            except Exception:
                snap["together"] = {"ok": False, "error": "無法連線 (timeout)"}
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return snap

    # ── quota HTML page (all backends) ─────────────────────────────────────
    @staticmethod
    def _quota_page(snap: dict) -> bytes:
        import html as _html

        def esc(s) -> str:
            return _html.escape(str(s or ""))

        def bar(pct: float, color: str) -> str:
            w = max(0.0, min(100.0, pct))
            return ("<div class='bar'><div class='fill' "
                    f"style='width:{w:.1f}%;background:{color}'></div></div>")

        # ── Antigravity: group models into shared quota pools ──
        # Pool identity = (remaining fraction, reset time): models drawing
        # from the same pool always report identical values.
        ag = snap.get("antigravity") or {}
        ag_models = ag.get("models") or [] if ag.get("ok") else []
        catalog_keys = {str(m.get("key")) for m in ag_models if m.get("key")}
        pools: dict = {}
        for m in ag_models:
            key = str(m.get("key") or "")
            sig = (round(float(m.get("remaining") or 0), 4), str(m.get("reset_time") or "")[:19])
            pools.setdefault(sig, []).append(m)

        def _family(key: str) -> str:
            k = key.lower()
            if k.startswith("chat_"):
                return "Chat"
            if k.startswith("tab_"):
                return "Tab"
            if k.startswith("gpt-"):
                return "GPT-OSS"
            return k.split("-")[0].capitalize()

        pool_cards, attention = [], []
        for (frac, _reset19), members in pools.items():
            pct = frac * 100
            fams = sorted({_family(str(x.get("key") or "")) for x in members})
            if set(fams) <= {"Chat", "Tab"}:
                pname = "Chat / Tab 內部共用額度"
            else:
                pname = f"{' / '.join(f for f in fams if f not in ('Chat', 'Tab'))} 共用額度 Shared pool"
            if frac <= 0:
                status, cls, color, rank = "用完 Exhausted", "red", "#dc2626", 0
            elif frac <= 0.1:
                status, cls, color, rank = "偏低 Low", "yellow", "#ca8a04", 1
            else:
                status, cls, color, rank = "可用 OK", "green", "#16a34a", 2
            if rank < 2:
                attention.append(f"{pname} ({pct:.1f}%)")
            chips = "".join(
                f"<span class='chip' title='{esc(x.get('name') or x.get('key'))}'>{esc(x.get('key'))}</span>"
                for x in sorted(members, key=lambda x: str(x.get("key"))))
            reset_iso = next((str(x.get("reset_time") or "") for x in members if x.get("reset_time")), "")
            keys = [str(x.get("key") or "") for x in members]
            caveats = []
            if any(k.startswith("chat_") for k in keys):
                caveats.append("chat_* 有額度但不接受對話請求（實測 HTTP 400）")
            if any(k.startswith("tab_") for k in keys):
                caveats.append("tab_* 為 Tab 補全模型：可回應文字，但為自動完成調校")
            caveat_html = f"<div class='note'>{esc('；'.join(caveats))}</div>" if caveats else ""
            pool_cards.append((rank, pname.startswith("Chat / Tab"), pname,
                "<div class='pool'>"
                f"<div class='pool-head'><b>{esc(pname)}</b> <span class='pill {cls}'>{status}</span></div>"
                f"<div class='pool-bar'>{bar(pct, color)}<span class='pct'>{pct:.2f}% 剩餘</span></div>"
                f"<div class='pool-row'><span class='reset' data-reset='{esc(reset_iso)}'>—</span>"
                f"<span class='note'>{len(members)} 個模型</span></div>"
                f"<div class='chips'>{chips}</div>"
                f"{caveat_html}"
                "</div>"))
        pool_cards.sort(key=lambda r: (r[0], r[1], r[2]))
        pools_html = "\n".join(c[3] for c in pool_cards) if pool_cards else "<div class='note'>無資料</div>"

        # ── known aliases whose app-side id is NOT in the catalog ──
        by_target: dict = {}
        for friendly, target in AntigravityAppBackend.MODEL_ALIASES.items():
            by_target.setdefault(str(target), []).append(str(friendly))
        missing = sorted(t for t in by_target if t not in catalog_keys)
        if not ag.get("ok"):
            missing_block = ""
        elif missing:
            rows = "\n".join(
                "<div class='miss-row'>"
                f"<span><b>{esc(' / '.join(by_target[t]))}</b> → <span class='key'>{esc(t)}</span></span>"
                "<span class='pill gray'>無此模型 Not in catalog</span>"
                "</div>" for t in missing)
            missing_block = (
                "<details class='fold'><summary>"
                f"不在目錄中的已知模型 ({len(missing)}) — 預先設定的別名或已被下架" 
                f"</summary>{rows}</details>")
        else:
            missing_block = "<div class='allok'>✅ 所有已知模型都在目錄中</div>"

        pools_note = "<div class='note'>同一池內的模型共用額度：用量扣同一池的重設時間一致。</div>" if pools else ""

        if not ag.get("ok"):
            attention_html = f"<div class='alert red'>⚠️ Google AI Pro 配額讀取失敗：{esc(ag.get('error', ''))}</div>"
        elif attention:
            attention_html = f"<div class='alert yellow'>⚠️ 需要注意：{esc('、'.join(attention))}</div>"
        else:
            attention_html = "<div class='alert green'>✅ 所有可用模型額度充足</div>"

        # ── header pills ──
        ag_pill = (f"<span class='pill green'>Google AI Pro · {len(pools)} 個共用額度</span>"
                   if ag.get("ok") else "<span class='pill red'>Google AI Pro · 讀取失敗</span>")
        oc = snap.get("opencode") or {}
        if oc.get("ok"):
            roll = next((x for x in (oc.get("models") or []) if x.get("key") == "rolling"), None)
            oc_pill = (f"<span class='pill green'>OpenCode Go · 今日剩餘 {float(roll.get('remaining', 0)) * 100:.0f}%</span>"
                       if roll is not None else "<span class='pill green'>OpenCode Go · 已連線</span>")
        else:
            oc_pill = f"<span class='pill gray'>OpenCode Go · {esc(oc.get('error', '未知'))}</span>"
        to = snap.get("together") or {}
        if to.get("ok"):
            to_pill = f"<span class='pill green'>Together AI · {len(to.get('models') or [])} 個模型</span>"
        else:
            to_pill = f"<span class='pill gray'>Together AI · {esc(to.get('error', '未知'))}</span>"

        # ── OpenCode Go usage cards ──
        if oc.get("ok"):
            cards = []
            for u in oc.get("models") or []:
                frac = float(u.get("remaining") or 0)
                pct = frac * 100
                color = "#16a34a" if frac > 0.5 else ("#ca8a04" if frac > 0.1 else "#dc2626")
                cards.append(
                    "<div class='card'>"
                    f"<div class='card-name'>{esc(u.get('name') or u.get('key'))}</div>"
                    f"{bar(pct, color)}<div class='card-row'><span class='pct'>{pct:.1f}% 剩餘</span>"
                    f"<span class='reset' data-reset='{esc(u.get('reset_time', ''))}'>—</span></div>"
                    "</div>")
            oc_block = f"<div class='cards'>{''.join(cards)}</div>" if cards else "<div class='note'>無額度資料</div>"
        else:
            oc_block = f"<div class='note'>{esc(oc.get('error', ''))} — 設定 OPENCODE_API_KEY 後會顯示用量</div>"

        # ── Together AI model chips ──
        if to.get("ok"):
            ids = [str(x.get("id")) for x in (to.get("models") or []) if x.get("id")]
            chips = "".join(f"<span class='chip'>{esc(i)}</span>" for i in ids[:60])
            more = f"<span class='note'>＋{len(ids) - 60} more</span>" if len(ids) > 60 else ""
            to_block = f"<div class='chips'>{chips}{more}</div>" if ids else "<div class='note'>金鑰有效，但沒有回傳模型</div>"
        else:
            to_block = f"<div class='note'>{esc(to.get('error', ''))} — 設定 TOGETHER_API_KEY 後會顯示模型</div>"

        fetched = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.get("fetched_at", time.time())))
        html = f"""<!doctype html>
<html lang="zh-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>AI Token 額度 / Quota Monitor</title>
<style>
 body {{ font-family: -apple-system, 'Segoe UI', 'Noto Sans TC', sans-serif; margin: 0;
        background: #0f172a; color: #e2e8f0; }}
 .wrap {{ max-width: 900px; margin: 0 auto; padding: 24px 16px; }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }}
 h2 {{ font-size: 16px; margin: 28px 0 10px; color: #e2e8f0; }}
 .sub {{ color: #94a3b8; font-size: 13px; margin-bottom: 12px; }}
 .pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 14px; }}
 .pill {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
 .pill.green {{ background: #14532d; color: #bbf7d0; }}
 .pill.yellow {{ background: #713f12; color: #fde68a; }}
 .pill.red {{ background: #7f1d1d; color: #fecaca; }}
 .pill.gray {{ background: #334155; color: #cbd5e1; }}
 .alert {{ padding: 10px 14px; border-radius: 8px; font-size: 14px; margin-bottom: 6px; }}
 .alert.green {{ background: #052e16; color: #bbf7d0; }}
 .alert.yellow {{ background: #451a03; color: #fde68a; }}
 .alert.red {{ background: #450a0a; color: #fecaca; }}
 #filter {{ width: 100%; box-sizing: border-box; padding: 8px 12px; margin: 4px 0 8px;
      background: #1e293b; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; font-size: 14px; }}
 #count {{ color: #64748b; font-size: 12px; margin-bottom: 4px; }}
 .pool {{ background: #0b1220; border: 1px solid #1e293b; border-radius: 10px;
      padding: 14px 16px; margin-bottom: 12px; }}
 .pool-head {{ display: flex; justify-content: space-between; align-items: center;
      gap: 10px; font-size: 15px; margin-bottom: 10px; }}
 .pool-bar {{ display: flex; align-items: center; gap: 4px; }}
 .pool-bar .bar {{ flex: 1; }}
 .pool-row {{ display: flex; justify-content: space-between; margin: 6px 0 10px; }}
 .pool .chips {{ margin-top: 2px; }}
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
 details.fold {{ margin-top: 10px; background: #0b1220; border: 1px solid #1e293b;
      border-radius: 8px; padding: 10px 14px; font-size: 13px; }}
 details.fold summary {{ cursor: pointer; color: #94a3b8; }}
 .miss-row {{ display: flex; justify-content: space-between; align-items: center;
      gap: 10px; padding: 8px 0; border-bottom: 1px solid #1e293b; }}
 .allok {{ color: #86efac; font-size: 13px; margin-top: 8px; }}
 .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
 .card {{ background: #0b1220; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 14px; }}
 .card-name {{ font-size: 14px; margin-bottom: 8px; }}
 .card-row {{ display: flex; justify-content: space-between; margin-top: 6px; }}
 .chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
 .chip {{ background: #1e293b; border-radius: 6px; padding: 3px 9px; font-size: 12px;
      font-family: monospace; color: #cbd5e1; }}
 .note {{ color: #64748b; font-size: 13px; }}
</style></head><body><div class="wrap">
<h1>🎫 AI Token 額度 / Quota Monitor</h1>
<div class="sub">每 60 秒自動更新 · 資料擷取於 {fetched}（本機時間）</div>
<div class="pills">{ag_pill} {oc_pill} {to_pill}</div>
{attention_html}
<h2>Google AI Pro — 共用額度 Pools</h2>
<input id="filter" placeholder="篩選… / filter pools & models…" oninput="filt(this.value)">
<div id="count"></div>
<div id="pools">
{pools_html}
</div>
{pools_note}
{missing_block}
<h2>OpenCode Go — 訂閱用量</h2>
{oc_block}
<h2>Together AI — 模型</h2>
{to_block}
<script>
function tick() {{
  document.querySelectorAll('.reset[data-reset]').forEach(el => {{
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
function filt(q) {{
  q = q.toLowerCase();
  const cards = document.querySelectorAll('#pools .pool');
  let n = 0;
  cards.forEach(c => {{
    const hit = c.textContent.toLowerCase().includes(q);
    c.style.display = hit ? '' : 'none';
    if (hit) n++;
  }});
  document.getElementById('count').textContent = n + ' / ' + cards.length + ' 個共用額度';
}}
 document.getElementById('count').textContent =
  document.querySelectorAll('#pools .pool').length + ' 個共用額度';
tick(); setInterval(tick, 1000);
</script></body></html>"""
        return html.encode()


def main() -> int:
    ap = argparse.ArgumentParser(description="Sloth AI bridge (Google AI Pro / Antigravity)")
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
    raise SystemExit(main())

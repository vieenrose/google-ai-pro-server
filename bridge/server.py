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
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from gemini_backends import build_prompt, pick_backend  # noqa: E402
from deep_research import run_research  # noqa: E402

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
            if url.startswith("//"):
                url = "https:" + url if FORUM_BASE.startswith("https") else "http:" + url
            return f"![generated image]({url})"
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[bridge] upload failed: {e}\n")
    return ""


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "GeminiBridge/0.2"
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
        else:
            self._send(404, {"error": "not found"})

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
        else:
            self._send(404, {"error": "not found"})

    def _chat(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.5-flash"
        if not messages:
            self._send(400, {"error": "messages required"})
            return
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

    # ── OpenAI-compatible chat completions ──────────────────────────────────
    def _chat_completions(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.5-flash"
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")
        if not messages:
            self._error(400, "messages required", "invalid_request_error")
            return
        if body.get("stream"):
            self._stream_completions(messages, model, tools=tools, tool_choice=tool_choice)
            return

        try:
            result = self.backend.chat_messages(messages, model=model, tools=tools, tool_choice=tool_choice)
        except Exception as e:  # noqa: BLE001
            self._error(500, str(e), "server_error")
            return
        if result.error:
            self._error(502, result.error, "upstream_error")
            return

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

    def _stream_completions(self, messages, model, tools=None, tool_choice=None) -> None:
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
        try:
            chunk(role_done=True)
            for delta, meta in self.backend.chat_messages_stream(messages, model=model, tools=tools, tool_choice=tool_choice):
                if delta:
                    chunk(delta_text=delta)
                if meta and meta.get("type") == "tool_call":
                    tool_chunk(meta["tool_call"])
                if meta and meta.get("type") == "image":
                    _images.append(meta)
            for img in _images:
                md = upload_to_forum(img.get("mimeType", "image/png"), img.get("data", ""))
                if md:
                    chunk(delta_text="\n\n" + md)
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
    ap.add_argument("--backend", choices=["agy", "direct", "gemini", "mock"],
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

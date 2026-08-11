#!/usr/bin/env python3
"""
bridge/server.py — tiny HTTP bridge between Discourse and the Gemini AI Pro demo.

Endpoints (all require `Authorization: Bearer <BRIDGE_TOKEN>` if set):

    GET  /health                → {ok, backend, authenticated}
    POST /v1/chat               → {reply, model, usage, sources}
        body: {"messages": [{role, content}...], "model": "gemini-3.5-flash"}
    POST /v1/deep-research      → {report, sources, plan, questions}
        body: {"topic": "...", "max_questions": 3, "model": "gemini-3.5-flash"}
        ⚠ takes 1–5 minutes (call from a background job, not the HTTP request path)

Backend: the same pluggable layer as the CLI demo (agy = live Google Search,
direct = knowledge-only, mock = offline). Choose with --backend.

Usage:
    BRIDGE_TOKEN=secret python3 bridge/server.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from gemini_backends import build_prompt, pick_backend  # noqa: E402
from deep_research import run_research  # noqa: E402


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "GeminiBridge/0.1"
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

    # ── OpenAI-compatible chat completions (for Discourse AI) ───────────────
    def _chat_completions(self, body: dict) -> None:
        messages = body.get("messages") or []
        model = body.get("model") or "gemini-3.5-flash"
        if not messages:
            self._send(400, {"error": {"message": "messages required",
                                       "type": "invalid_request_error", "param": None, "code": None}})
            return
        if body.get("stream"):
            self._stream_completions(messages, model)
            return

        try:
            result = self.backend.chat_messages(messages, model=model)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": {"message": str(e), "type": "server_error", "param": None, "code": None}})
            return
        if result.error:
            self._send(502, {"error": {"message": result.error, "type": "upstream_error", "param": None, "code": None}})
            return

        usage = result.usage or {}
        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": result.model or model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        }
        self._send(200, resp)

    def _stream_completions(self, messages, model) -> None:
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

        try:
            chunk(role_done=True)
            for delta, _meta in self.backend.chat_messages_stream(messages, model=model):
                if delta:
                    chunk(delta_text=delta)
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
    args = ap.parse_args()

    BridgeHandler.backend = pick_backend(args.backend)
    BridgeHandler.token = os.environ.get("BRIDGE_TOKEN", "")
    b = BridgeHandler.backend
    print(f"[bridge] backend: {b.name} — {b.describe()}", flush=True)
    print(f"[bridge] auth: {'token required' if BridgeHandler.token else 'open (no token)'}", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"[bridge] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())

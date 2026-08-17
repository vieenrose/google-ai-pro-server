#!/usr/bin/env python3
"""
gemini_backends.py — pluggable backends for the Gemini AI Pro demo.

All backends consume the *Google AI Pro subscription* (no Gemini API key):

  1. AgyBackend         — official Antigravity CLI (`agy`) headless mode.
                          Uses the AI Pro OAuth credentials cached by `agy`
                          (browser sign-in once, then headless forever).
  2. DirectTokenBackend — talks straight to Google's internal Antigravity /
                          Cloud Code Assist backend (cloudcode-pa.googleapis.com)
                          using the OAuth token `agy` cached
                          (~/.gemini/antigravity-cli/antigravity-oauth-token).
                          Technique recycled from the open-source community
                          proxy (usamashehab/antigravity-proxy).
  3. GeminiCliBackend   — legacy `gemini` CLI (predecessor of agy), still
                          installed on some machines.
  4. MockBackend        — offline simulator so the demo runs anywhere.

Every backend exposes the same tiny interface:

    backend.chat(prompt, model, stream=False)   -> ChatResult
    backend.chat_stream(prompt, model)          -> iterable of (delta_text, meta)
    backend.available() -> bool
    backend.describe()  -> str
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

try:
    import urllib.request
    import urllib.error
except ImportError:  # pragma: no cover
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChatResult:
    text: str
    model: str = ""
    usage: dict = field(default_factory=dict)
    conversation_id: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)
    finish_reason: str = ""


def build_prompt(messages: list[dict], system: str = "") -> str:
    """Turn an OpenAI-style message list into a single prompt for headless CLIs."""
    parts: list[str] = []
    if system:
        parts.append(f"System: {system}")
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):  # multimodal blocks
            texts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif isinstance(b, dict) and b.get("type") == "image_url":
                    texts.append("[image]")
            content = "\n".join(texts)
        label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role.capitalize())
        parts.append(f"{label}: {content}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Backend 1: official Antigravity CLI (agy)
# ─────────────────────────────────────────────────────────────────────────────

class AgyBackend:
    """Spawns `agy -p ... --output-format json|stream-json`."""

    name = "agy"

    # friendly name -> Antigravity backend model id (tested 2026-08)
    # Gemini 3.x models use a -low/-medium/-high suffix on this backend.
    MODEL_ALIASES = {
        "gemini-3.6-flash": "gemini-3.6-flash-low",
        "gemini-3.7-flash": "gemini-3.7-flash",  # in catalog? not yet (404 as of 2026-08-13)
        "gemini-3.5-flash": "gemini-3.5-flash-low",
        "gemini-3-flash": "gemini-3-flash",
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3.1-pro-high": "gemini-3.1-pro-low",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash": "gemini-2.5-flash",
        "claude-sonnet-4.6": "claude-sonnet-4-6",
        "claude-opus-4.6": "claude-opus-4-6-thinking",
        "gpt-oss-120b": "gpt-oss-120b",
    }

    def __init__(self, bin_name: str | None = None):
        self.bin = bin_name or os.environ.get("AGY_BIN") or shutil.which("agy") or ""

    # -- interface ------------------------------------------------------------
    def available(self) -> bool:
        return bool(self.bin) and Path(self.bin).exists() if self.bin else False

    def describe(self) -> str:
        return f"AgyBackend (agy={self.bin or 'NOT FOUND'}) — official Antigravity CLI, uses your AI Pro OAuth login"

    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        if stream:
            out = ChatResult(text="")
            for delta, _meta in self.chat_stream(prompt, model):
                out.text += delta
            return out
        args = self._base_args(prompt, model, "json")
        code, stdout, stderr = _run(args, timeout=300)
        if code != 0:
            return ChatResult(text="", error=_agy_error(stderr))
        try:
            env = json.loads(stdout)
        except json.JSONDecodeError:
            return ChatResult(text="", error=f"agy returned non-JSON: {stdout[:300]}")
        if env.get("status") == "SUCCESS":
            return ChatResult(
                text=env.get("response", ""),
                model=model,
                usage=env.get("usage") or {},
                conversation_id=env.get("conversation_id", ""),
                raw=env,
            )
        return ChatResult(text="", error=env.get("error") or f"agy status={env.get('status')}")

    def chat_stream(self, prompt: str, model: str = "gemini-3.5-flash"):
        args = self._base_args(prompt, model, "stream-json")
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("event")
            payload = ev.get(etype) or ev.get("step_update") or {}
            if etype == "step_update":
                delta = payload.get("text_delta")
                if delta:
                    yield delta, {"type": payload.get("step_type", "agent_response")}
            elif etype == "result" and payload.get("status") != "SUCCESS":
                yield "", {"error": payload.get("error", "unknown agy error")}
        proc.wait(timeout=300)
        if proc.returncode != 0:
            err = proc.stderr.read() if proc.stderr else ""
            yield "", {"error": _agy_error(err)}

    # -- helpers --------------------------------------------------------------
    def _base_args(self, prompt: str, model: str, output_format: str) -> list[str]:
        m = self.MODEL_ALIASES.get(model, model)
        args = [self.bin, "-p", prompt, "--output-format", output_format]
        if m:
            args += ["--model", m]
        # local personal automation: don't block on permission prompts
        args += ["--dangerously-skip-permissions"]
        return args


def _agy_error(stderr: str) -> str:
    s = stderr.strip()
    if not s:
        return "agy failed (exit code != 0)"
    # agy prints a json error object at the end sometimes
    try:
        idx = s.rindex("{")
        err = json.loads(s[idx:])
        return f"{err.get('message') or err.get('error') or s} (code {err.get('code')})"
    except Exception:
        return s.splitlines()[-1] if s else "agy failed"


# ─────────────────────────────────────────────────────────────────────────────
# Backend 2: direct Cloud Code Assist API (recycled community technique)
# ─────────────────────────────────────────────────────────────────────────────

CLOUDCODE_BASE = "https://cloudcode-pa.googleapis.com"
AGY_OAUTH_TOKEN = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# The Antigravity agent tries to call internal tools (search etc.) that we don't
# execute here. Steering it to answer from knowledge keeps responses usable.
DEFAULT_SYSTEM_INSTRUCTION = (
    "You are a knowledgeable, helpful AI assistant. Answer directly from your "
    "own knowledge and write well-structured text. Do NOT call any tools or "
    "functions — never output tool calls, just plain text."
)
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Antigravity/1.0.14 Chrome/138.0.7204.235 "
               "Electron/37.3.1 Safari/537.36")
_CLIENT_METADATA = json.dumps({"ideType": "ANTIGRAVITY", "platform": "MACOS", "pluginType": "GEMINI"}, separators=(",", ":"))


def _content_to_parts(content):
    """OpenAI content (string or parts array) -> Gemini parts (text + inlineData)."""
    if isinstance(content, list):
        parts = []
        for el in content:
            if not isinstance(el, dict):
                continue
            if el.get("type") == "text" and el.get("text"):
                parts.append({"text": str(el["text"])})
            elif el.get("type") == "image_url":
                url = (el.get("image_url") or {}).get("url") or ""
                if url.startswith("data:image/"):
                    head, _, b64 = url.partition(",")
                    mime = head[5:].split(";")[0]
                    parts.append({"inlineData": {"mimeType": mime, "data": b64}})
            elif el.get("type") == "file":
                fd = (el.get("file") or {})
                file_data = fd.get("file_data") or ""
                if file_data.startswith("data:"):
                    head, _, b64 = file_data.partition(",")
                    mime = head[5:].split(";")[0]
                    parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        return parts
    return [{"text": str(content)}] if content is not None else []


class DirectTokenBackend:
    """Reuses the agy OAuth token and calls cloudcode-pa.googleapis.com directly.

    Technique (envelope, headers, endpoints) recycled from the MIT-licensed
    community proxy https://github.com/usamashehab/antigravity-proxy
    """

    name = "direct"

    def __init__(self, token_file: str | None = None):
        self.token_file = Path(token_file or os.environ.get("ANTIGRAVITY_TOKEN_FILE") or AGY_OAUTH_TOKEN)
        self._token: dict = {}
        self._project_id: str = ""

    # -- interface ------------------------------------------------------------
    def available(self) -> bool:
        return self.token_file.exists()

    def describe(self) -> str:
        state = "token present" if self.available() else "no agy OAuth token found"
        return f"DirectTokenBackend ({state}) — cloudcode-pa.googleapis.com via agy token (community technique)"

    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        if stream:
            out = ChatResult(text="")
            for delta, _meta in self.chat_stream(prompt, model):
                out.text += delta
            return out
        try:
            resp = self._generate(prompt, model)
        except Exception as e:  # noqa: BLE001
            return ChatResult(text="", error=str(e))
        text, usage = _gemini_response_to_text(resp)
        return ChatResult(text=text, model=model, usage=usage, raw=resp)

    def chat_stream(self, prompt: str, model: str = "gemini-3.5-flash"):
        for event in self._generate_stream(prompt, model):
            for part in (event.get("candidates") or [{}])[0].get("content", {}).get("parts", []) or []:
                if part.get("text"):
                    yield part["text"], {"type": "text"}
            usage = event.get("usageMetadata")
            if usage:
                yield "", {"usage": usage}

    # -- plumbing -------------------------------------------------------------
    def _access_token(self) -> str:
        if not self._token or int(self._token.get("expiry", 0)) - time.time() < 120:
            self._token = _read_agy_token(self.token_file)
            if int(self._token.get("expiry", 0)) - time.time() < 120:
                self._token = _refresh_token(self._token)
        return self._token["access_token"]

    def _project(self) -> str:
        if not self._project_id:
            self._project_id = _load_code_assist(self._access_token())
        return self._project_id

    def _envelope(self, prompt: str, model: str) -> dict:
        inner = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": DEFAULT_SYSTEM_INSTRUCTION}]},
            "generationConfig": {},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                          "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
            ],
        }
        return {
            "project": self._project(),
            "model": AgyBackend.MODEL_ALIASES.get(model, model),
            "request": inner,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": f"agent-{uuid.uuid4().hex}",
        }

    @staticmethod
    def _extract_tool_calls(resp: dict) -> list:
        """Gemini functionCall parts -> OpenAI tool_calls."""
        calls = []
        for c in resp.get("candidates", []):
            for part in c.get("content", {}).get("parts", []) or []:
                fc = part.get("functionCall")
                if fc:
                    calls.append({
                        "id": f"call_{len(calls)}",
                        "type": "function",
                        "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False)},
                    })
        return calls

    def chat_messages(self, messages, model: str = "gemini-3.5-flash", stream: bool = False,
                      tools=None, tool_choice=None):
        """OpenAI-style messages (+tools) -> Gemini. Returns ChatResult (non-stream)."""
        try:
            resp = self._generate_envelope(self._envelope_messages(messages, model, tools, tool_choice))
        except Exception as e:  # noqa: BLE001
            return ChatResult(text="", error=str(e))
        text, usage = _gemini_response_to_text(resp)
        finish = (resp.get("candidates") or [{}])[0].get("finishReason", "")
        return ChatResult(text=text, model=model, usage=usage, raw=resp,
                          tool_calls=self._extract_tool_calls(resp),
                          finish_reason="tool_calls" if self._extract_tool_calls(resp) else ("stop" if finish == "STOP" else ""))

    def chat_messages_stream(self, messages, model: str = "gemini-3.5-flash", tools=None, tool_choice=None):
        """OpenAI-style messages (+tools) -> yields (delta, meta) tuples (streaming)."""
        for event in self._generate_stream_envelope(self._envelope_messages(messages, model, tools, tool_choice)):
            cand = (event.get("candidates") or [{}])[0]
            parts = cand.get("content", {}).get("parts", []) or []
            for part in parts:
                if part.get("text"):
                    yield part["text"], {"type": "text"}
                if part.get("inlineData"):
                    d = part["inlineData"]
                    yield "", {"type": "image", "mimeType": d.get("mimeType", "image/png"), "data": d.get("data", "")}
                fc = part.get("functionCall")
                if fc:
                    yield "", {"type": "tool_call", "tool_call": {
                        "id": f"call_{abs(hash(fc.get('name','')))}",
                        "type": "function",
                        "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False)},
                    }}
            usage = event.get("usageMetadata")
            if usage:
                yield "", {"usage": usage}

    def _envelope_messages(self, messages, model: str, tools=None, tool_choice=None) -> dict:
        system_parts, contents = [], []
        for m in messages:
            role = (m or {}).get("role", "user")
            content = (m or {}).get("content")
            if role == "system":
                if content is not None:
                    system_parts.append({"text": str(content)})
            elif role == "tool":
                # tool result from a previous call -> functionResponse (role: function)
                name = (m.get("name") or m.get("tool_call_id") or "tool")
                payload = content if content is not None else ""
                try:
                    parsed = json.loads(payload) if isinstance(payload, str) else payload
                    response_obj = parsed if isinstance(parsed, dict) else {"result": parsed}
                except Exception:
                    response_obj = {"result": payload}
                fr = {"name": name, "response": response_obj}
                # NOTE: streaming endpoint rejects role "function"; "user" works on both
                contents.append({"role": "user", "parts": [{"functionResponse": fr}]})
            elif role == "assistant":
                tc = (m or {}).get("tool_calls") or []
                parts = []
                if content:
                    parts.append({"text": str(content)})
                for call in tc:
                    fn = call.get("function", {}) if isinstance(call, dict) else {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    fc_obj = {"name": fn.get("name", ""), "args": args}
                    call_id = call.get("id", "") if isinstance(call, dict) else ""
                    if call_id and call_id.startswith("call_"):
                        fc_obj["id"] = call_id[5:].split("|")[0]
                    part_obj = {"functionCall": fc_obj}
                    # sentinel required by the API validator for functionCall parts
                    part_obj["thoughtSignature"] = "skip_thought_signature_validator"
                    parts.append(part_obj)
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:
                if content is None:
                    next
                parts = _content_to_parts(content)
                if parts:
                    contents.append({"role": "user", "parts": parts})
        default_system = DEFAULT_SYSTEM_INSTRUCTION
        if tools and not system_parts:
            # tools present + no user system prompt: allow tool use
            default_system = "You are a helpful AI assistant. Use the provided tools when they help answer the user."
        inner = {
            "contents": contents or [{"role": "user", "parts": [{"text": "hi"}]}],
            "systemInstruction": {"parts": system_parts or [{"text": default_system}]},
            "generationConfig": {},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                          "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
            ],
        }
        # OpenAI tools -> Gemini functionDeclarations
        if tools:
            decls = []
            for t in tools:
                fn = (t or {}).get("function", {}) if isinstance(t, dict) else {}
                decl = {"name": fn.get("name", "tool"), "description": fn.get("description", "")}
                if fn.get("parameters"):
                    decl["parameters"] = fn["parameters"]
                decls.append(decl)
            inner["tools"] = [{"functionDeclarations": decls}]
            if tool_choice:
                cfg = {"mode": "AUTO"}
                if tool_choice == "none":
                    cfg["mode"] = "NONE"
                elif tool_choice == "required":
                    cfg["mode"] = "ANY"
                elif isinstance(tool_choice, dict):
                    fn = (tool_choice.get("function") or {})
                    cfg["mode"] = "ANY"
                    if fn.get("name"):
                        cfg["allowedFunctionNames"] = [fn["name"]]
                inner["toolConfig"] = {"functionCallingConfig": cfg}
        return {
            "project": self._project(),
            "model": AgyBackend.MODEL_ALIASES.get(model, model),
            "request": inner,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": f"agent-{uuid.uuid4().hex}",
        }

    def _generate_envelope(self, envelope: dict, retries: int = 3) -> dict:
        body = json.dumps(envelope).encode()
        for attempt in range(retries):
            status, raw = _http_post(f"{CLOUDCODE_BASE}/v1internal:generateContent", body, self._access_token())
            if status == 429 and attempt < retries - 1:
                time.sleep(20 * (attempt + 1))
                continue
            if status != 200:
                raise RuntimeError(f"cloudcode generateContent HTTP {status}: {raw[:400]}")
            payload = json.loads(raw)
            return payload.get("response", payload)
        raise RuntimeError("cloudcode generateContent retries exhausted")

    def _generate_stream_envelope(self, envelope: dict, retries: int = 3):
        body = json.dumps(envelope).encode()
        for attempt in range(retries):
            first = None
            first_data = None
            for status, data in _http_post_stream(f"{CLOUDCODE_BASE}/v1internal:streamGenerateContent?alt=sse",
                                                  body, self._access_token()):
                if status != 200:
                    if status == 429 and attempt < retries - 1:
                        first = "429"
                        first_data = data
                        break
                    yield {"error": {"message": f"HTTP {status}: {data[:300]}"}}
                    return
                payload = json.loads(data)
                inner = payload.get("response", payload)
                if inner:
                    yield inner
            if first == "429":
                time.sleep(20 * (attempt + 1))
                continue
            return

    def _generate(self, prompt: str, model: str) -> dict:
        return self._generate_envelope(self._envelope(prompt, model))

    def _generate_stream(self, prompt: str, model: str):
        yield from self._generate_stream_envelope(self._envelope(prompt, model))


# ─────────────────────────────────────────────────────────────────────────────
# Backend 3: legacy gemini CLI (fallback)
# ─────────────────────────────────────────────────────────────────────────────

class GeminiCliBackend:
    name = "gemini"

    def __init__(self, bin_name: str | None = None):
        self.bin = bin_name or os.environ.get("GEMINI_BIN") or shutil.which("gemini") or ""

    def available(self) -> bool:
        return bool(self.bin)

    def describe(self) -> str:
        return f"GeminiCliBackend (gemini={self.bin or 'NOT FOUND'}) — legacy CLI, deprecated for Google One users"

    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        if stream:
            out = ChatResult(text="")
            for delta, _meta in self.chat_stream(prompt, model):
                out.text += delta
            return out
        args = [self.bin, "-p", prompt, "-o", "json", "--approval-mode", "yolo"]
        if model:
            args += ["-m", model]
        code, stdout, stderr = _run(args, timeout=300)
        if code != 0:
            return ChatResult(text="", error=_agy_error(stderr))
        try:
            env = json.loads(stdout)
        except json.JSONDecodeError:
            return ChatResult(text="", error=f"gemini returned non-JSON: {stdout[:300]}")
        if env.get("status") == "SUCCESS":
            return ChatResult(text=env.get("response", ""), model=model,
                              usage=env.get("usage") or {}, raw=env)
        return ChatResult(text="", error=env.get("error") or f"gemini status={env.get('status')}")

    def chat_stream(self, prompt: str, model: str = "gemini-3.5-flash"):
        args = [self.bin, "-p", prompt, "-o", "stream-json", "--approval-mode", "yolo"]
        if model:
            args += ["-m", model]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = ev.get("step_update") or ev
            if isinstance(payload, dict) and payload.get("text_delta"):
                yield payload["text_delta"], {"type": "text"}
        proc.wait(timeout=300)


# ─────────────────────────────────────────────────────────────────────────────
# Backend 4: offline mock (demo anywhere, no auth)
# ─────────────────────────────────────────────────────────────────────────────

class MockBackend:
    """Deterministic simulated backend — proves the CLI UX without credentials."""

    name = "mock"

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "MockBackend — offline simulator (no auth needed); switch with /backend"

    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        if stream:
            out = ChatResult(text="")
            for delta, _m in self.chat_stream(prompt, model):
                out.text += delta
            return out
        time.sleep(0.6)
        return ChatResult(
            text=_mock_reply(prompt),
            model=model,
            usage={"input_tokens": 120, "output_tokens": 180, "total_tokens": 300},
            conversation_id=f"mock-{uuid.uuid4().hex[:12]}",
        )

    def chat_stream(self, prompt: str, model: str = "gemini-3.5-flash"):
        reply = _mock_reply(prompt)
        words = reply.split(" ")
        for i, w in enumerate(words):
            time.sleep(0.02)
            yield w + (" " if i < len(words) - 1 else ""), {"type": "text"}


def _mock_reply(prompt: str) -> str:
    """Context-aware canned responses so the demo shows the real workflow UX."""
    if "Create a focused research plan" in prompt:
        return (
            "1. What are the current market size and growth projections for the topic?\n"
            "2. What are the key technology or product developments in the last 12 months?\n"
            "3. Who are the main players and what are the competitive dynamics?\n"
            "4. What are the main risks, challenges, and regulatory considerations?"
        )
    if prompt.startswith("Research the following question"):
        q = prompt.split("Question:", 1)[-1].strip()[:60]
        return (
            f"Notes on: {q}\n\n"
            "- Market data: the segment is growing ~20% YoY, driven by enterprise adoption [1].\n"
            "- Recent developments: several major launches in Q2 2026, including new flagship\n"
            "  products from incumbents and startups alike [2].\n"
            "- Competitive dynamics: two clear leaders, a fast-following challenger, and\n"
            "  growing open-source alternatives [3].\n"
            "- Risks: regulatory scrutiny in the EU, talent shortage, and supply-chain\n"
            "  concentration [4].\n\n"
            "## Sources\n"
            "[1] https://example.com/market-report-2026\n"
            "[2] https://example.com/q2-2026-launches\n"
            "[3] https://example.com/competitive-landscape\n"
            "[4] https://example.com/regulatory-risks"
        )
    if prompt.startswith("You are writing the final report"):
        return (
            "# Research Report\n\n"
            "## 1. Executive Summary\n"
            "The topic is experiencing rapid growth (~20% YoY), with enterprise adoption "
            "as the primary driver [1]. Two leaders dominate, but open-source alternatives "
            "are closing the gap [3].\n\n"
            "## 2. Market Overview\n"
            "Market size continues to expand across all major regions, with the fastest "
            "growth in APAC [1].\n\n"
            "## 3. Key Developments (last 12 months)\n"
            "Q2 2026 saw several flagship launches, indicating an accelerating product "
            "cycle [2].\n\n"
            "## 4. Risks & Outlook\n"
            "EU regulatory scrutiny and supply-chain concentration are the main headwinds "
            "[4]. Overall outlook remains positive.\n\n"
            "## Sources\n"
            "[1] https://example.com/market-report-2026\n"
            "[2] https://example.com/q2-2026-launches\n"
            "[3] https://example.com/competitive-landscape\n"
            "[4] https://example.com/regulatory-risks"
        )
    topic = prompt.strip()[:80]
    return (
        f"[MOCK] You asked: “{topic}”.\n\n"
        "This is a simulated Gemini reply so the demo runs without credentials. "
        "Install Antigravity CLI and sign in with your Google AI Pro account, then "
        "the same commands will hit the real subscription backend via `agy`.\n\n"
        "Tips: /deep <topic> runs the Deep Research workflow, /backend switches "
        "backends, /model changes the model."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"binary not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def _read_agy_token(path: Path) -> dict:
    data = json.loads(path.read_text())
    if "token" in data and isinstance(data["token"], dict):
        data = data["token"]  # unwrap agy's {"token": {...}} envelope
    expiry = data.get("expiry")
    if isinstance(expiry, str):
        try:
            expiry = float(expiry)
        except ValueError:
            # RFC3339 string like 2026-06-30T12:55:03.123456789Z
            from datetime import datetime
            try:
                s = expiry[:26] + "Z" if "Z" in expiry[26:] else expiry[:26]
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                expiry = dt.timestamp()
            except Exception:
                expiry = 0.0
    data["expiry"] = float(expiry or 0)
    return data


# OAuth client embedded in every Antigravity CLI binary (public credential).
# Kept out of git (GitHub secret scanning blocks the literal value); read from
# the environment when a token refresh is needed. Get it from the community
# proxy repo (usamashehab/antigravity-proxy) or extract it from the agy binary.
_AGY_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
_AGY_CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")


def _refresh_token(token: dict) -> dict:
    refresh = token.get("refresh_token")
    if not refresh:
        raise RuntimeError("OAuth token has no refresh_token — re-auth with auth_agy.py")
    if not _AGY_CLIENT_SECRET:
        raise RuntimeError(
            "ANTIGRAVITY_CLIENT_SECRET env var not set (needed to refresh the token). "
            "See demo/README.md for where to find it."
        )
    body = json.dumps({
        "refresh_token": refresh,
        "client_id": token.get("client_id", _AGY_CLIENT_ID),
        "client_secret": token.get("client_secret", _AGY_CLIENT_SECRET),
        "grant_type": "refresh_token",
    }).encode()
    status, raw = _http_post(OAUTH_TOKEN_URL, body, "")
    if status != 200:
        raise RuntimeError(f"token refresh failed HTTP {status}: {raw[:300]}")
    payload = json.loads(raw)
    payload["expiry"] = time.time() + int(payload.get("expires_in", 3600)) - 60
    payload["refresh_token"] = refresh
    return payload


def _load_code_assist(access_token: str) -> str:
    status, raw = _http_post(f"{CLOUDCODE_BASE}/v1internal:loadCodeAssist", b"{}", access_token)
    if status != 200:
        raise RuntimeError(f"loadCodeAssist HTTP {status}: {raw[:400]}")
    payload = json.loads(raw)
    pid = payload.get("cloudaicompanionProject")
    if not pid:  # recursive search
        def _find(o):
            if isinstance(o, dict):
                if "cloudaicompanionProject" in o:
                    return o["cloudaicompanionProject"]
                for v in o.values():
                    r = _find(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = _find(v)
                    if r:
                        return r
            return None
        pid = _find(payload)
    if not pid:
        raise RuntimeError(f"no project id in loadCodeAssist response: {str(payload)[:300]}")
    return pid


def _http_post(url: str, body: bytes, access_token: str, timeout: int = 120) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, method="POST")
    if access_token:
        req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("X-Goog-Api-Client", "google-cloud-sdk vscode_cloudshelleditor/0.1")
    req.add_header("Client-Metadata", _CLIENT_METADATA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _http_post_stream(url: str, body: bytes, access_token: str, timeout: int = 120):
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("X-Goog-Api-Client", "google-cloud-sdk vscode_cloudshelleditor/0.1")
    req.add_header("Client-Metadata", _CLIENT_METADATA)
    req.add_header("Accept", "text/event-stream")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        yield e.code, e.read().decode()
        return
    buf = ""
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        buf += chunk.decode(errors="replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if line.startswith("data:"):
                yield 200, line[5:].strip()


def _gemini_response_to_text(resp: dict) -> tuple[str, dict]:
    parts = []
    for cand in resp.get("candidates", []) or []:
        for part in (cand.get("content") or {}).get("parts", []) or []:
            if part.get("text"):
                parts.append(part["text"])
    return "\n".join(parts), resp.get("usageMetadata") or {}


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiApiBackend:
    """Official Gemini API (generativelanguage.googleapis.com) via an AI Studio
    key. The key belongs to the same Google account that holds the Google AI
    Pro subscription, so usage is drawn from the subscription's API tier.
    """

    name = "gemini-api"

    # forum slug -> official API model id (verified 2026-08-13)
    MODEL_ALIASES = {
        "gemini-3.6-flash": "gemini-3.6-flash",
        "gemini-3.5-flash": "gemini-3.5-flash",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
        "gemini-3-flash": "gemini-3-flash-preview",
        "gemini-2.5-flash": "gemini-3.1-flash-lite",  # retired upstream
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-2.5-pro": "gemini-3.1-pro-preview",  # retired upstream
        "gemini-3.1-flash-image": "gemini-3.1-flash-image",
        "nano-banana": "nano-banana-pro-preview",
    }

    # models that only exist on the cloudcode (Antigravity) side — the bridge
    # replies with a clear note instead of failing silently.
    CLOUDCODE_ONLY = {
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b",
    }

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def available(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        return (
            "GeminiApiBackend — official Gemini API "
            "(generativelanguage.googleapis.com) with AI Studio key"
        )

    # -- helpers -------------------------------------------------------------
    def _api_model(self, model: str) -> str:
        return self.MODEL_ALIASES.get(model, model)

    @staticmethod
    def _default_body() -> dict:
        return {
            "generationConfig": {},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }

    def _api_post(self, model: str, body: dict, timeout: int = 300) -> dict:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                msg = json.loads(raw).get("error", {}).get("message", raw[:300])
            except Exception:
                msg = raw[:300]
            return {"__error__": f"Gemini API HTTP {e.code}: {msg[:400]}"}
        except Exception as e:  # noqa: BLE001
            return {"__error__": f"Gemini API request failed: {e}"}

    def _api_stream(self, model: str, body: dict, timeout: int = 300):
        url = (
            f"{GEMINI_API_BASE}/models/{model}:streamGenerateContent"
            f"?key={self.api_key}&alt=sse"
        )
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                msg = json.loads(raw).get("error", {}).get("message", raw[:300])
            except Exception:
                msg = raw[:300]
            yield json.dumps({"__error__": f"Gemini API HTTP {e.code}: {msg[:400]}"})
            return
        buf = ""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("data:"):
                    yield line[5:].strip()

    @staticmethod
    def _extract_tool_calls(resp: dict) -> list:
        calls = []
        for cand in resp.get("candidates", []) or []:
            for part in (cand.get("content") or {}).get("parts", []) or []:
                fc = part.get("functionCall")
                if fc:
                    calls.append({
                        "id": f"call_{len(calls)}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                        },
                    })
        return calls

    def _envelope_messages(self, messages, model: str, tools=None, tool_choice=None) -> dict:
        api_model = self._api_model(model)
        is_gemini3 = api_model.startswith("gemini-3")
        system_parts, contents = [], []
        for m in messages:
            role = (m or {}).get("role", "user")
            content = (m or {}).get("content")
            if role == "system":
                if content is not None:
                    system_parts.append({"text": str(content)})
            elif role == "tool":
                name = (m or {}).get("name") or (m or {}).get("tool_call_id") or "tool"
                payload = content if content is not None else ""
                try:
                    parsed = json.loads(payload) if isinstance(payload, str) else payload
                    response_obj = parsed if isinstance(parsed, dict) else {"result": parsed}
                except Exception:
                    response_obj = {"result": payload}
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {"name": name, "response": response_obj}}],
                })
            elif role == "assistant":
                tc = (m or {}).get("tool_calls") or []
                parts = []
                if content:
                    parts.append({"text": str(content)})
                for call in tc:
                    fn = call.get("function", {}) if isinstance(call, dict) else {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    fc_obj = {"name": fn.get("name", ""), "args": args}
                    if is_gemini3:
                        # required by the API validator for thinking models
                        fc_obj["thoughtSignature"] = "skip_thought_signature_validator"
                    parts.append({"functionCall": fc_obj})
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:
                if content is None:
                    continue
                parts = _content_to_parts(content)
                if parts:
                    contents.append({"role": "user", "parts": parts})
        body = self._default_body()
        body["contents"] = contents or [{"role": "user", "parts": [{"text": "hi"}]}]
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}
        if tools:
            decls = []
            for t in tools:
                fn = (t or {}).get("function", {}) if isinstance(t, dict) else {}
                decl = {"name": fn.get("name", "tool"), "description": fn.get("description", "")}
                if fn.get("parameters"):
                    decl["parameters"] = fn["parameters"]
                decls.append(decl)
            body["tools"] = [{"functionDeclarations": decls}]
            cfg = {"mode": "AUTO"}
            if tool_choice == "none":
                cfg["mode"] = "NONE"
            elif tool_choice == "required":
                cfg["mode"] = "ANY"
            elif isinstance(tool_choice, dict):
                fn = (tool_choice.get("function") or {})
                cfg["mode"] = "ANY"
                if fn.get("name"):
                    cfg["allowedFunctionNames"] = [fn["name"]]
            body["toolConfig"] = {"functionCallingConfig": cfg}
        return body

    # -- interface ------------------------------------------------------------
    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        if model in self.CLOUDCODE_ONLY:
            return ChatResult(
                text="",
                error=(
                    f"模型 `{model}` 只存在於 Antigravity/cloudcode 通道，"
                    "目前無法透過 Gemini API 提供。"
                ),
            )
        api_model = self._api_model(model)
        body = self._default_body()
        body["contents"] = [{"role": "user", "parts": [{"text": prompt}]}]
        resp = self._api_post(api_model, body)
        if resp.get("__error__"):
            return ChatResult(text="", error=resp["__error__"])
        text, usage = _gemini_response_to_text(resp)
        return ChatResult(text=text, model=model, usage=usage, raw=resp)

    def chat_messages(
        self, messages, model: str = "gemini-3.5-flash", stream: bool = False,
        tools=None, tool_choice=None,
    ) -> ChatResult:
        if model in self.CLOUDCODE_ONLY:
            return ChatResult(
                text="",
                error=(
                    f"模型 `{model}` 只存在於 Antigravity/cloudcode 通道，"
                    "目前無法透過 Gemini API 提供。"
                ),
            )
        api_model = self._api_model(model)
        resp = self._api_post(api_model, self._envelope_messages(messages, model, tools, tool_choice))
        if resp.get("__error__"):
            return ChatResult(text="", error=resp["__error__"])
        text, usage = _gemini_response_to_text(resp)
        finish = (resp.get("candidates") or [{}])[0].get("finishReason", "")
        calls = self._extract_tool_calls(resp)
        return ChatResult(
            text=text,
            model=model,
            usage=usage,
            raw=resp,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else ("stop" if finish == "STOP" else ""),
        )

    def chat_messages_stream(self, messages, model: str = "gemini-3.5-flash", tools=None, tool_choice=None):
        if model in self.CLOUDCODE_ONLY:
            yield (
                f"\n\n⚠️ 模型 `{model}` 只存在於 Antigravity/cloudcode 通道，"
                "目前無法透過 Gemini API 提供。",
                None,
            )
            return
        api_model = self._api_model(model)
        for raw in self._api_stream(api_model, self._envelope_messages(messages, model, tools, tool_choice)):
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("__error__"):
                yield f"\n\n[bridge: {event['__error__']}]", None
                continue
            # Only the first candidate — upstream occasionally returns two
            # candidates with identical text, which duplicated bot replies.
            for cand in (event.get("candidates") or [])[:1]:
                for part in (cand.get("content") or {}).get("parts", []) or []:
                    if part.get("text"):
                        yield part["text"], None
                    if part.get("inlineData"):
                        d = part["inlineData"]
                        yield "", {
                            "type": "image",
                            "mimeType": d.get("mimeType", "image/png"),
                            "data": d.get("data", ""),
                        }
                    fc = part.get("functionCall")
                    if fc:
                        yield "", {
                            "type": "tool_call",
                            "tool_call": {
                                "id": f"call_{abs(hash(fc.get('name', '')))}",
                                "type": "function",
                                "function": {
                                    "name": fc.get("name", ""),
                                    "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                                },
                            },
                        }
            if event.get("usageMetadata"):
                yield "", {"usage": event["usageMetadata"]}


class AntigravityAppBackend(GeminiApiBackend):
    """Antigravity application's subscription-backed inference path.

    This is distinct from ``cloudcode-pa.googleapis.com`` (the Code Assist
    endpoint used by the older direct backend). Antigravity uses the daily
    Cloud Code control plane plus an agent envelope containing the model enum,
    session/trajectory identifiers, and labels. The model catalog and quota
    are fetched from the same control plane, so paid Google One / G1 models
    are selected by Google's backend rather than by a client-side tier flag.

    This endpoint is private/undocumented and may change with the application.
    """

    name = "antigravity-app"
    ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
    APP_USER_AGENT = "antigravity/cli/1.0.1 linux/arm64"
    APP_SYSTEM_INSTRUCTION = "You are Antigravity, a helpful agentic AI assistant."

    MODEL_ALIASES = {
        "gemini-3.6-flash": "gemini-3.6-flash-low",
        "gemini-3.7-flash": "gemini-3.7-flash",  # in catalog? not yet (404 as of 2026-08-13)
        "gemini-3.5-flash": "gemini-3.5-flash-low",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
        "gemini-3-flash": "gemini-3-flash",
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3.1-pro-preview": "gemini-3.1-pro-low",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-opus-4-6": "claude-opus-4-6-thinking",
        "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
        "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    }

    def __init__(self, token_file: str | None = None):
        self.token_file = Path(
            token_file or os.environ.get("ANTIGRAVITY_TOKEN_FILE") or AGY_OAUTH_TOKEN
        )
        self._token: dict = {}
        self._project_id = ""
        self._models: dict = {}

    def available(self) -> bool:
        return self.token_file.exists()

    def describe(self) -> str:
        state = "OAuth token present" if self.available() else "no OAuth token found"
        return (
            f"AntigravityAppBackend ({state}) — daily Cloud Code application "
            "backend; uses Google One/AI Pro subscription quota"
        )

    def _access_token(self) -> str:
        if not self._token or int(self._token.get("expiry", 0)) - time.time() < 120:
            self._token = _read_agy_token(self.token_file)
            if int(self._token.get("expiry", 0)) - time.time() < 120:
                self._token = _refresh_token(self._token)
        return self._token["access_token"]

    @staticmethod
    def _decode_response(raw: bytes, headers) -> dict:
        if headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode(errors="replace"))

    def _post(self, method: str, payload: dict, timeout: int = 180, _attempt: int = 0) -> dict:
        req = urllib.request.Request(
            f"{self.ENDPOINT}/v1internal:{method}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {self._access_token()}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", self.APP_USER_AGENT)
        req.add_header("Accept-Encoding", "gzip")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return self._decode_response(response.read(), response.headers)
        except urllib.error.HTTPError as e:
            if e.code == 429 and _attempt < 3:
                time.sleep(3 * (_attempt + 1))
                return self._post(method, payload, timeout, _attempt + 1)
            try:
                body = self._decode_response(e.read(), e.headers)
                message = body.get("error", {}).get("message", str(body))
            except Exception:
                message = e.read().decode(errors="replace")[:500]
            if e.code == 429:
                import re as _re
                m = _re.search(r"reset after ([0-9]+h[0-9]+m[0-9]+s)", message)
                hint = f"（約 {m.group(1)} 後恢復）" if m else ""
                return {
                    "__error__": f"模型暫時繁忙（伺服器容量限制，非訂閱額度）{hint}。請稍後重試。"
                }
            return {"__error__": f"Antigravity app HTTP {e.code}: {message[:500]}"}
        except Exception as e:  # noqa: BLE001
            return {"__error__": f"Antigravity app request failed: {e}"}

    def quota(self) -> dict:
        """Per-model subscription quota from the Antigravity control plane.

        Returns {fetched_at, models: [{key, name, remaining, reset_time}]}
        cached for 60s. Mirrors the Antigravity Quota Monitor VS Code
        extension (same fetchAvailableModels + quotaInfo source).
        """
        now = time.time()
        if getattr(self, "_quota_cache", None) and now - self._quota_cache["t"] < 60:
            return self._quota_cache["data"]
        response = self._post("fetchAvailableModels", {"project": self._project()})
        if response.get("__error__"):
            raise RuntimeError(response["__error__"])
        models = []
        for key, cfg in sorted((response.get("models") or {}).items()):
            q = cfg.get("quotaInfo") or {}
            frac = q.get("remainingFraction")
            if frac is None:
                continue
            models.append({
                "key": key,
                "name": cfg.get("displayName") or key,
                "remaining": round(float(frac), 4),
                "reset_time": q.get("resetTime") or "",
            })
        data = {"fetched_at": now, "models": models}
        self._quota_cache = {"t": now, "data": data}
        return data

    def _stream(self, method: str, payload: dict, timeout: int = 120, _attempt: int = 0):
        req = urllib.request.Request(
            f"{self.ENDPOINT}/v1internal:{method}?alt=sse",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {self._access_token()}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        req.add_header("User-Agent", self.APP_USER_AGENT)
        req.add_header("Accept-Encoding", "gzip")
        try:
            response = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 429 and _attempt < 3:
                time.sleep(3 * (_attempt + 1))
                yield from self._stream(method, payload, timeout, _attempt + 1)
                return
            if e.code == 429:
                import re as _re
                try:
                    body = self._decode_response(e.read(), e.headers)
                    message = body.get("error", {}).get("message", str(body))
                except Exception:
                    message = e.read().decode(errors="replace")[:500]
                m = _re.search(r"reset after ([0-9]+h[0-9]+m[0-9]+s)", message)
                hint = f"（約 {m.group(1)} 後恢復）" if m else ""
                yield {"__error__": f"模型暫時繁忙（伺服器容量限制，非訂閱額度）{hint}。請稍後重試。"}
                return
            try:
                body = self._decode_response(e.read(), e.headers)
                message = body.get("error", {}).get("message", str(body))
            except Exception:
                message = e.read().decode(errors="replace")[:500]
            yield {"__error__": f"Antigravity app HTTP {e.code}: {message[:500]}"}
            return
        raw = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        for line in raw.decode(errors="replace").splitlines():
            if line.startswith("data:"):
                value = line[5:].strip()
                if value and value != "[DONE]":
                    try:
                        yield json.loads(value)
                    except json.JSONDecodeError:
                        continue

    def _project(self) -> str:
        if not self._project_id:
            response = self._post("loadCodeAssist", {"metadata": {"ideType": "ANTIGRAVITY"}})
            if response.get("__error__"):
                raise RuntimeError(response["__error__"])
            self._project_id = response.get("cloudaicompanionProject", "")
            if not self._project_id:
                raise RuntimeError("Antigravity app returned no Cloud AI Companion project")
        return self._project_id

    def _model_config(self, model: str) -> tuple[str, dict]:
        app_model = self.MODEL_ALIASES.get(model, model)
        if not self._models:
            response = self._post("fetchAvailableModels", {"project": self._project()})
            if response.get("__error__"):
                raise RuntimeError(response["__error__"])
            self._models = response.get("models", {})
        config = self._models.get(app_model, {})
        if not config:
            raise RuntimeError(f"Antigravity app model is unavailable: {app_model}")
        return app_model, config

    def _request_payload(self, messages, model, tools=None, tool_choice=None) -> tuple[dict, str]:
        app_model, model_config = self._model_config(model)
        body = self._envelope_messages(messages, model, tools, tool_choice)
        # Antigravity's application path exposes Google Search as a native
        # provider tool. This uses the subscription-backed app quota, not the
        # Gemini Developer API/CSE quota.
        native_tools = list(body.get("tools") or [])
        if not any(isinstance(t, dict) and "googleSearch" in t for t in native_tools):
            native_tools.append({"googleSearch": {}})
        body["tools"] = native_tools
        system = body.get("systemInstruction") or {"parts": []}
        system_parts = system.get("parts") or []
        if not system_parts or system_parts[0].get("text") != self.APP_SYSTEM_INSTRUCTION:
            system = {"parts": [{"text": self.APP_SYSTEM_INSTRUCTION}, *system_parts]}

        generation = dict(body.get("generationConfig") or {})
        if "maxOutputTokens" not in generation and model_config.get("maxOutputTokens"):
            generation["maxOutputTokens"] = model_config["maxOutputTokens"]
        if model_config.get("supportsThinking") and "thinkingConfig" not in generation:
            # Budget must be >= 1024 (server constraint). includeThoughts returns the
            # reasoning as parts flagged with `thought`, which we fold into a
            # <details class='ai-thinking'> block in the reply (same as Gemini).
            generation["thinkingConfig"] = {
                "thinkingBudget": max(int(model_config.get("thinkingBudget") or 1024), 1024),
                "includeThoughts": True,
            }
        if app_model == "gemini-3.1-flash-image":
            generation.setdefault("responseModalities", ["TEXT", "IMAGE"])

        tool_config = body.get("toolConfig") or {
            "functionCallingConfig": {"mode": "VALIDATED"}
        }
        if tools:
            fc = tool_config.setdefault("functionCallingConfig", {})
            if tool_choice == "none":
                fc["mode"] = "NONE"
            elif tool_choice == "required":
                fc["mode"] = "ANY"
            else:
                fc["mode"] = "VALIDATED"

        digest = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        conversation_id = str(uuid.UUID(digest[:32]))
        trajectory_id = str(uuid.uuid4())
        step_index = max(3, len(messages))
        session_id = str(-abs(int(digest[32:48], 16)))
        request_id = f"agent/{conversation_id}/{int(time.time() * 1000)}/{trajectory_id}/{step_index}"
        model_enum = model_config.get("model", "MODEL_PLACEHOLDER_M187")

        request = {
            "contents": body.get("contents", []),
            "systemInstruction": system,
            "tools": body.get("tools", []),
            "toolConfig": tool_config,
            "labels": {
                "last_step_index": str(step_index - 1),
                "model_enum": model_enum,
                "trajectory_id": trajectory_id,
                "used_claude": "false",
                "used_claude_conservative": "false",
            },
            "generationConfig": generation,
            "sessionId": session_id,
        }
        return {
            "project": self._project_id,
            "requestId": request_id,
            "request": request,
            "model": app_model,
            "userAgent": "antigravity",
            "requestType": "agent",
        }, app_model

    @staticmethod
    def _response_body(response: dict) -> dict:
        if response.get("__error__"):
            return response
        return response.get("response", response)

    @staticmethod
    def _grounding_markdown(response: dict) -> str:
        candidates = response.get("candidates") or []
        if not candidates:
            return ""
        metadata = candidates[0].get("groundingMetadata") or {}
        links = []
        for chunk in metadata.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            uri, title = web.get("uri"), web.get("title") or web.get("domain")
            if uri and uri not in [u for u, _ in links]:
                links.append((uri, title or uri))
        if not links:
            return ""
        return "\n\n### Sources\n" + "\n".join(
            f"- [{title}]({uri})" for uri, title in links[:10]
        )

    @staticmethod
    def _extract_text_with_thinking(response: dict) -> str:
        thinking, answer = [], []
        for cand in response.get("candidates", []) or []:
            for part in (cand.get("content") or {}).get("parts", []) or []:
                if "thought" in part:
                    if part.get("text"):
                        thinking.append(part["text"])
                elif part.get("text"):
                    answer.append(part["text"])
        out = ""
        if thinking:
            out += (
                "<details class='ai-thinking'><summary>Thinking</summary>\n\n"
                + "\n\n".join(thinking)
                + "\n\n</details>\n\n"
            )
        out += "\n\n".join(answer)
        return out

    def chat(self, prompt: str, model: str = "gemini-3.5-flash", stream: bool = False) -> ChatResult:
        return self.chat_messages([{"role": "user", "content": prompt}], model=model)

    def chat_messages(self, messages, model: str = "gemini-3.5-flash", stream: bool = False,
                      tools=None, tool_choice=None) -> ChatResult:
        try:
            payload, app_model = self._request_payload(messages, model, tools, tool_choice)
            response = self._response_body(self._post("generateContent", payload))
        except Exception as e:  # noqa: BLE001
            return ChatResult(text="", error=str(e))
        if response.get("__error__"):
            return ChatResult(text="", error=response["__error__"])
        text = self._extract_text_with_thinking(response)
        text += self._grounding_markdown(response)
        calls = self._extract_tool_calls(response)
        finish = (response.get("candidates") or [{}])[0].get("finishReason", "")
        return ChatResult(
            text=text,
            model=model,
            usage=response.get("usageMetadata", {}),
            raw=response,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else ("stop" if finish == "STOP" else ""),
        )

    def chat_messages_stream(self, messages, model: str = "gemini-3.5-flash", tools=None, tool_choice=None):
        try:
            payload, app_model = self._request_payload(messages, model, tools, tool_choice)
        except Exception as e:  # noqa: BLE001
            yield f"\n\n[bridge error: {e}]", None
            return
        grounding_response = None
        in_thinking = False
        for event in self._stream("streamGenerateContent", payload):
            if event.get("__error__"):
                yield f"\n\n[bridge error: {event['__error__']}]", None
                continue
            response = self._response_body(event)
            grounding_response = response
            # Only the first candidate — upstream occasionally returns two
            # candidates with identical text, which duplicated bot replies.
            for cand in (response.get("candidates") or [])[:1]:
                for part in (cand.get("content") or {}).get("parts", []) or []:
                    is_thought = "thought" in part
                    text = part.get("text", "")
                    if is_thought:
                        if not in_thinking:
                            yield "\n\n<details class='ai-thinking'><summary>Thinking</summary>\n\n", None
                            in_thinking = True
                        if text:
                            yield text, None
                        continue
                    # answer / non-thought part
                    if in_thinking:
                        yield "\n\n</details>\n\n", None
                        in_thinking = False
                    if text:
                        yield text, None
                    if part.get("inlineData"):
                        data = part["inlineData"]
                        yield "", {
                            "type": "image",
                            "mimeType": data.get("mimeType", "image/png"),
                            "data": data.get("data", ""),
                        }
                    fc = part.get("functionCall")
                    if fc:
                        yield "", {
                            "type": "tool_call",
                            "tool_call": {
                                "id": fc.get("id") or f"call_{abs(hash(fc.get('name', '')))}",
                                "type": "function",
                                "function": {
                                    "name": fc.get("name", ""),
                                    "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                                },
                            },
                        }
            if response.get("usageMetadata"):
                yield "", {"usage": response["usageMetadata"]}
        if in_thinking:
            yield "\n\n</details>\n\n", None
        if grounding_response:
            citations = self._grounding_markdown(grounding_response)
            if citations:
                yield citations, None


def pick_backend(prefer: str | None = None) -> object:
    """Auto-select the best available backend (agy > app > direct > API > mock)."""
    order = [AgyBackend(), AntigravityAppBackend(), DirectTokenBackend(), GeminiApiBackend(), GeminiCliBackend()]
    if prefer:
        for b in order:
            if b.name == prefer:
                return b
        return MockBackend() if prefer == "mock" else MockBackend()
    for b in order:
        if b.available():
            return b
    return MockBackend()


# ─────────────────────────────────────────────────────────────────────────────
# Backend 6: OpenCode.ai Zen Go (OpenAI-compatible, e.g. deepseek-v4-flash)
# ─────────────────────────────────────────────────────────────────────────────
OPENCODE_BASE_URL = os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
OPENCODE_API_KEY = os.environ.get("OPENCODE_API_KEY", "")


class OpenCodeBackend:
    """OpenAI-compatible endpoint at opencode.ai/zen/go (deepseek-v4-* etc.).

    No native grounding — the bridge injects SearXNG results for these models
    (searxng.inject is already applied for any non-Gemini model).
    """

    name = "opencode"

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or OPENCODE_BASE_URL).rstrip("/")
        self.api_key = api_key or OPENCODE_API_KEY

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def describe(self) -> str:
        return f"OpenCodeBackend ({self.base_url}) — OpenAI-compatible, SearXNG-grounded"

    UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

    def _get(self, path: str, timeout: int = 180) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}")
        req.add_header("User-Agent", self.UA)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode(errors="replace"))

    def quota(self) -> dict:
        """OpenCode Go subscription usage (rolling/weekly/monthly percent + resets).

        Mirrors what the OpenCode account page shows. Returns a dict shaped
        like the Antigravity quota() so the bridge's quota page can render it:
        {fetched_at, models: [{key, name, remaining, reset_time}],}
        plus a top-level "opencode": {...} block with the raw usage.
        """
        now = time.time()
        if getattr(self, "_quota_cache", None) and now - self._quota_cache["t"] < 60:
            return self._quota_cache["data"]
        try:
            data = self._get("/usage", timeout=30)
        except Exception as e:  # noqa: BLE001
            return {"fetched_at": now, "error": str(e), "models": []}
        usage = data.get("usage") or {}
        models = []
        for roll in ("rolling", "weekly", "monthly"):
            u = usage.get(roll) or {}
            pct = u.get("percent")
            if pct is None:
                continue
            models.append({
                "key": roll,
                "name": {"rolling": "今日額度 (rolling)",
                         "weekly": "本週額度 (weekly)",
                         "monthly": "本月額度 (monthly)"}[roll],
                "remaining": max(0.0, 1.0 - float(pct) / 100.0),
                "reset_time": u.get("resetsAt", ""),
            })
        out = {"fetched_at": now, "models": models, "opencode": usage}
        self._quota_cache = {"t": now, "data": out}
        return out

    def list_models(self) -> list[dict]:
        """OpenCode Go models relevant to the forum bots (deepseek/mimo).

        Returns [{id, family}] — used by the Sloth AI admin page to offer
        bot creation for every usable OpenCode model.
        """
        try:
            data = self._get("/models", timeout=30)
        except Exception:  # noqa: BLE001
            return []
        ids = [m.get("id", "") for m in (data.get("data") or [])]
        return [
            {"id": i, "family": i.split("-")[0] if "-" in i else i}
            for i in ids
            if i.startswith(("deepseek", "mimo"))
        ]

    def _post(self, path: str, body: dict, timeout: int = 180) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", self.UA)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode(errors="replace"))

    def chat(self, prompt: str, model: str = "deepseek-v4-flash", stream: bool = False) -> ChatResult:
        result = self.chat_messages([{"role": "user", "content": prompt}], model=model)
        return result

    def chat_messages(self, messages, model: str = "deepseek-v4-flash", stream: bool = False,
                      tools=None, tool_choice=None) -> ChatResult:
        body: dict = {"model": model, "messages": messages, "stream": False}
        try:
            data = self._post("/chat/completions", body)
        except Exception as e:  # noqa: BLE001
            return ChatResult(text="", error=str(e))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ChatResult(
            text=message.get("content") or "",
            model=model,
            usage=data.get("usage") or {},
            finish_reason=choice.get("finish_reason") or "stop",
        )

    def chat_stream(self, prompt: str, model: str = "deepseek-v4-flash"):
        yield from self.chat_messages_stream([{"role": "user", "content": prompt}], model=model)

    def chat_messages_stream(self, messages, model: str = "deepseek-v4-flash", tools=None, tool_choice=None):
        body: dict = {"model": model, "messages": messages, "stream": True}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", self.UA)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            resp = urllib.request.urlopen(req, timeout=180)
        except Exception as e:  # noqa: BLE001
            yield f"\n\n[bridge error: {e}]", None
            return
        for raw_line in resp:
            line = raw_line.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in ev.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text, None

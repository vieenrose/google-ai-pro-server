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

    def chat_messages(self, messages, model: str = "gemini-3.5-flash", stream: bool = False):
        """OpenAI-style [{role, content}] -> Gemini. Returns ChatResult (non-stream)."""
        try:
            resp = self._generate_envelope(self._envelope_messages(messages, model))
        except Exception as e:  # noqa: BLE001
            return ChatResult(text="", error=str(e))
        text, usage = _gemini_response_to_text(resp)
        return ChatResult(text=text, model=model, usage=usage, raw=resp)

    def chat_messages_stream(self, messages, model: str = "gemini-3.5-flash"):
        """OpenAI-style [{role, content}] -> yields (delta, meta) tuples (streaming)."""
        for event in self._generate_stream_envelope(self._envelope_messages(messages, model)):
            for part in (event.get("candidates") or [{}])[0].get("content", {}).get("parts", []) or []:
                if part.get("text"):
                    yield part["text"], {"type": "text"}
            usage = event.get("usageMetadata")
            if usage:
                yield "", {"usage": usage}

    def _envelope_messages(self, messages, model: str) -> dict:
        system_parts, contents = [], []
        for m in messages:
            role = (m or {}).get("role", "user")
            content = (m or {}).get("content")
            if content is None:
                continue
            if role == "system":
                system_parts.append({"text": str(content)})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": str(content)}]})
            else:
                contents.append({"role": "user", "parts": [{"text": str(content)}]})
        inner = {
            "contents": contents or [{"role": "user", "parts": [{"text": "hi"}]}],
            "systemInstruction": {"parts": system_parts or [{"text": DEFAULT_SYSTEM_INSTRUCTION}]},
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

    def _generate_envelope(self, envelope: dict) -> dict:
        body = json.dumps(envelope).encode()
        status, raw = _http_post(f"{CLOUDCODE_BASE}/v1internal:generateContent", body, self._access_token())
        if status != 200:
            raise RuntimeError(f"cloudcode generateContent HTTP {status}: {raw[:400]}")
        payload = json.loads(raw)
        return payload.get("response", payload)

    def _generate_stream_envelope(self, envelope: dict):
        body = json.dumps(envelope).encode()
        for status, data in _http_post_stream(f"{CLOUDCODE_BASE}/v1internal:streamGenerateContent?alt=sse",
                                              body, self._access_token()):
            if status != 200:
                yield {"error": {"message": f"HTTP {status}: {data[:300]}"}}
                return
            payload = json.loads(data)
            inner = payload.get("response", payload)
            if inner:
                yield inner

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


def _http_post_stream(url: str, body: bytes, access_token: str, timeout: int = 300):
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


def pick_backend(prefer: str | None = None) -> object:
    """Auto-select the best available backend (agy > direct > gemini > mock)."""
    order = [AgyBackend(), DirectTokenBackend(), GeminiCliBackend()]
    if prefer:
        for b in order:
            if b.name == prefer:
                return b
        return MockBackend() if prefer == "mock" else MockBackend()
    for b in order:
        if b.available():
            return b
    return MockBackend()

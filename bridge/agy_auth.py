"""agy_auth.py — Antigravity (Google AI Pro) auth status + re-auth flow.

Used by the Sloth AI admin page:
  GET  /v1/config/antigravity-auth         → status (account, expiry, health)
  POST /v1/config/antigravity-auth/url     → {auth_url, verifier} (PKCE start)
  POST /v1/config/antigravity-auth/exchange→ {code, verifier} → save token

Reuses auth_agy.py (PKCE helpers, token file layout) so the CLI and the web
admin stay consistent.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

from auth_agy import (  # noqa: E402
    TOKEN_FILE,
    build_auth_url,
    exchange_code,
    load_pkce_state,
    make_pkce,
    save_pkce_state,
    save_token,
)

ANTIGRAVITY_CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")


def _read_token() -> dict:
    try:
        data = json.loads(TOKEN_FILE.read_text())
        tok = data.get("token", data)
        return tok if isinstance(tok, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _decode_id_token(id_token: str) -> dict:
    if not id_token:
        return {}
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        pad = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return {}


def _parse_expiry(expiry) -> float:
    if not expiry:
        return 0.0
    if isinstance(expiry, (int, float)):
        return float(expiry)
    try:
        s = str(expiry)[:26] + ("Z" if "Z" in str(expiry)[26:] else "")
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def status() -> dict:
    """Health + status of the Google AI Pro (Antigravity) subscription auth."""
    tok = _read_token()
    claims = _decode_id_token(tok.get("id_token", ""))
    expiry = _parse_expiry(tok.get("expiry"))
    now = time.time()
    has_token = bool(tok.get("access_token"))
    has_refresh = bool(tok.get("refresh_token"))
    expired = has_token and expiry > 0 and expiry < now
    expires_in_s = max(0, int(expiry - now)) if expiry > 0 else None

    # Refresh health: try an actual token refresh (cheap, validates the
    # refresh token + client secret) and report the FRESH expiry — the bridge
    # refreshes in-memory anyway, so the stored file may be stale.
    refresh_ok = None
    refresh_error = None
    fresh_expiry = expiry
    if has_refresh:
        try:
            if not ANTIGRAVITY_CLIENT_SECRET:
                raise RuntimeError("ANTIGRAVITY_CLIENT_SECRET env not set")
            import gemini_backends as gb  # noqa: E402

            new_tok = gb._refresh_token(
                {
                    "refresh_token": tok["refresh_token"],
                    "client_id": tok.get("client_id", gb._AGY_CLIENT_ID),
                    "client_secret": tok.get("client_secret", ANTIGRAVITY_CLIENT_SECRET),
                }
            )
            refresh_ok = True
            fresh_expiry = float(new_tok.get("expiry") or expiry)
        except Exception as e:  # noqa: BLE001
            refresh_ok = False
            refresh_error = str(e)[:200]

    fresh_expires_in_s = max(0, int(fresh_expiry - now)) if fresh_expiry > 0 else None
    live_ok = has_token and fresh_expiry > now

    return {
        "ok": live_ok,
        "configured": has_token,
        "account": {
            "email": claims.get("email"),
            "name": claims.get("name"),
        },
        "token": {
            "expiry": datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()
            if expiry > 0 else None,
            "fresh_expiry": datetime.fromtimestamp(fresh_expiry, tz=timezone.utc).isoformat()
            if fresh_expiry > 0 else None,
            "expires_in_s": fresh_expires_in_s,
            "has_refresh": has_refresh,
            "stored_file_expired": expired,
        },
        "refresh": {
            "ok": refresh_ok,
            "error": refresh_error,
        },
        "client_secret_set": bool(ANTIGRAVITY_CLIENT_SECRET),
        "token_file": str(TOKEN_FILE),
    }


def start_reauth() -> dict:
    """Start PKCE re-auth: returns the Google sign-in URL + verifier."""
    verifier = make_pkce()[0]
    url = build_auth_url(verifier)
    save_pkce_state(verifier, "")
    return {"auth_url": url, "verifier": verifier, "note": "paste the callback code within 10 minutes"}


def complete_reauth(code: str, verifier: str | None = None) -> dict:
    """Exchange the callback code and save the fresh token."""
    from auth_agy import extract_code

    try:
        code = extract_code(code)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    verifier = verifier or load_pkce_state().get("verifier", "")
    if not verifier:
        return {"ok": False, "error": "no PKCE verifier — call /url first (or re-run the flow)"}
    try:
        payload = exchange_code(code, verifier)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
    if "access_token" not in payload:
        return {"ok": False, "error": f"exchange failed: {json.dumps(payload)[:200]}"}
    save_token(payload)
    return {"ok": True, "account": _decode_id_token(payload.get("id_token", "")).get("email")}

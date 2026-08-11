#!/usr/bin/env python3
"""
auth_agy.py — authenticate the Antigravity CLI (agy) with your Google account,
without needing a browser on this machine.

How it works:
  1. Generates a PKCE code_verifier + challenge and prints a Google sign-in URL.
  2. You open the URL in any browser, sign in with your Google AI Pro account,
     and approve the consent screen.
  3. You get redirected to https://antigravity.google/oauth-callback?code=...
     Copy the FULL redirected URL (or just the code parameter) and paste it here.
  4. The script exchanges the code for tokens and writes them to
     ~/.gemini/antigravity-cli/antigravity-oauth-token — the file `agy` reads.
  5. It verifies with a real `agy -p "..."` call.

Usage:
    python3 auth_agy.py                    # interactive (prints URL, reads code)
    python3 auth_agy.py --url              # just print the URL and exit
    python3 auth_agy.py --code <code>      # exchange a code you already have

Client credentials are the ones embedded in every Antigravity CLI binary
(public knowledge, recycled from the open-source community proxy).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
# OAuth client secret — public credential embedded in every Antigravity CLI
# binary. Kept out of git (GitHub secret scanning blocks the literal value);
# set it from your environment, or extract it from the agy binary / the
# community proxy repo (usamashehab/antigravity-proxy) when needed.
CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")
REDIRECT_URI = "https://antigravity.google/oauth-callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
    "https://www.googleapis.com/auth/aicode",
    "openid",
]
TOKEN_FILE = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_pkce() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(48))  # 64 chars
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_auth_url(verifier: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "state": b64url(secrets.token_bytes(16)),
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"token exchange failed HTTP {e.code}: {e.read().decode()[:500]}")


def save_token(payload: dict) -> None:
    exp_dt = datetime.now(timezone.utc) + __import__("datetime").timedelta(
        seconds=int(payload.get("expires_in", 3600)))
    token = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "token_type": payload.get("token_type", "Bearer"),
        "expiry": exp_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
        "scope": payload.get("scope", ""),
        "id_token": payload.get("id_token", ""),
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token}, indent=2))
    os.chmod(TOKEN_FILE, 0o600)
    print(f"✔ token saved to {TOKEN_FILE}")


def extract_code(pasted: str) -> str:
    """Accept a full callback URL or a bare code."""
    pasted = pasted.strip()
    if pasted.startswith("http"):
        q = urllib.parse.urlparse(pasted).query
        code = urllib.parse.parse_qs(q).get("code", [""])[0]
        if not code:
            raise ValueError("no 'code' parameter found in the pasted URL")
        return code
    return pasted


def verify_agy() -> bool:
    import subprocess
    agy = os.environ.get("AGY_BIN") or str(Path.home() / ".local" / "bin" / "agy")
    if not Path(agy).exists():
        agy = "agy"
    print("verifying with agy…")
    try:
        proc = subprocess.run(
            [agy, "-p", "Reply with exactly: auth ok", "--output-format", "json",
             "--dangerously-skip-permissions"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        print("(agy not found; token file written anyway)")
        return False
    if proc.returncode != 0:
        print(f"⚠ agy exited {proc.returncode}: {proc.stderr[-400:]}")
        return False
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"⚠ non-JSON output: {proc.stdout[:300]}")
        return False
    if env.get("status") == "SUCCESS":
        print(f"✔ agy works! response: {env.get('response', '')[:80]!r}")
        return True
    print(f"⚠ agy status={env.get('status')} error={env.get('error')}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Authenticate agy with your Google account (manual PKCE flow)")
    ap.add_argument("--url", action="store_true", help="print the sign-in URL and exit")
    ap.add_argument("--code", help="authorization code (or full callback URL) to exchange")
    args = ap.parse_args()

    verifier = make_pkce()[0]
    url = build_auth_url(verifier)

    if args.url:
        print(url)
        return 0

    print("1) Open this URL in any browser and sign in with your Google AI Pro account:")
    print()
    print("   " + url)
    print()
    if args.code:
        code = extract_code(args.code)
    else:
        print("2) After approving, you'll be redirected to")
        print("   https://antigravity.google/oauth-callback?code=...")
        print("   Copy the FULL redirected URL (or just the code) and paste it here:")
        try:
            code = extract_code(input("   > ").strip())
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1

    print("3) Exchanging code for tokens…")
    if not auth_agy.CLIENT_SECRET:
        print("✖ ANTIGRAVITY_CLIENT_SECRET env var is not set.")
        print("  Get it from the community proxy repo (usamashehab/antigravity-proxy)"
              " or extract it from the agy binary, then:"
              "  export ANTIGRAVITY_CLIENT_SECRET=...")
        return 1
    try:
        payload = exchange_code(code, verifier)
    except Exception as e:  # noqa: BLE001
        print(f"✖ {e}")
        return 1

    save_token(payload)
    verify_agy()
    print("\nDone. Now run:  python3 demo/cli.py --backend agy")
    return 0


if __name__ == "__main__":
    sys.exit(main())

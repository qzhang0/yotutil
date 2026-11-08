"""Yoto API client: sign-in and Make Your Own playlist uploads.

Uses the standard library only. Every HTTP call goes through `_request`, so
tests can replace that one function instead of touching the network.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from yotutil.config import CONFIG_DIR

logger = logging.getLogger(__name__)

AUTH_URL = "https://login.yotoplay.com/authorize"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
API_BASE = "https://api.yotoplay.com"
AUDIENCE = "https://api.yotoplay.com"
SCOPES = "user:content:manage offline_access"
# Must match the redirect URL registered on the app at dashboard.yoto.dev.
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8787
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"

TOKEN_FILE = CONFIG_DIR / "yoto_token.json"
# Refresh this many seconds before the access token actually expires.
_EXPIRY_MARGIN = 60
_LOGIN_TIMEOUT = 300


class YotoError(Exception):
    """Raised when talking to Yoto fails."""


def _request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
) -> dict | None:
    """Send one HTTP request and return the decoded JSON body, if any."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise YotoError(f"Yoto returned HTTP {e.code} for {method} {url}: {detail}")
    except urllib.error.URLError as e:
        raise YotoError(f"Could not reach {url}: {e.reason}")
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


# --- Sign-in -----------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """Return a PKCE (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "audience": AUDIENCE,
            "scope": SCOPES,
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def _wait_for_callback(state: str) -> str:
    """Serve one request on the loopback redirect URL and return the auth code."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            result.update(params)
            ok = "code" in params and params.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = (
                "Signed in to Yoto. You can close this tab."
                if ok
                else "Yoto sign-in failed. Check the terminal."
            )
            self.wfile.write(f"<p>{message}</p>".encode())

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler)
    except OSError as e:
        raise YotoError(f"Could not listen on {REDIRECT_URI}: {e}")
    server.timeout = _LOGIN_TIMEOUT
    with server:
        server.handle_request()

    if "error" in result:
        raise YotoError(
            f"Yoto sign-in failed: {result.get('error_description') or result['error']}"
        )
    if result.get("state") != state:
        raise YotoError("Yoto sign-in failed: no reply, or the reply didn't match this login.")
    if "code" not in result:
        raise YotoError("Yoto sign-in failed: no authorization code in the reply.")
    return result["code"]


def _token_request(fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    resp = _request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
    )
    if not resp or "access_token" not in resp:
        raise YotoError("Yoto sign-in failed: no access token in the reply.")
    return resp


def login(client_id: str, open_browser=webbrowser.open) -> None:
    """Sign in through the browser and save the tokens."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, challenge, state)
    print(f"Opening your browser to sign in to Yoto. If it doesn't open, visit:\n{url}")
    open_browser(url)
    code = _wait_for_callback(state)
    tokens = _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        }
    )
    _save_tokens(tokens)


def _save_tokens(tokens: dict, previous: dict | None = None) -> None:
    """Write tokens to TOKEN_FILE, readable only by the user."""
    stored = {
        "access_token": tokens["access_token"],
        # Keep the old refresh token only if Yoto didn't send a new one.
        "refresh_token": tokens.get("refresh_token")
        or (previous or {}).get("refresh_token"),
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(stored, f)
    os.chmod(TOKEN_FILE, 0o600)


def _jwt_exp(token: str) -> float:
    """The `exp` claim of a JWT, or 0 if it can't be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return 0.0


def access_token(client_id: str, now=time.time) -> str:
    """A valid access token, refreshed if it is about to expire."""
    if not TOKEN_FILE.exists():
        raise YotoError("Not signed in to Yoto. Run: yotutil yoto login")
    stored = json.loads(TOKEN_FILE.read_text())
    token = stored.get("access_token", "")
    if _jwt_exp(token) - _EXPIRY_MARGIN > now():
        return token

    refresh = stored.get("refresh_token")
    if not refresh:
        raise YotoError("Yoto sign-in has expired. Run: yotutil yoto login")
    logger.debug("Refreshing Yoto access token")
    try:
        tokens = _token_request(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh,
            }
        )
    except YotoError as e:
        raise YotoError(f"{e}\nYoto sign-in has expired. Run: yotutil yoto login")
    # Refresh tokens are single-use: the new one must replace the old.
    _save_tokens(tokens, previous=stored)
    return tokens["access_token"]

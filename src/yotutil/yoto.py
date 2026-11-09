"""Yoto API client: sign-in and Make Your Own playlist uploads.

Uses the standard library only. Every HTTP call goes through `_send`, and
every Yoto API call through `_request` on top of it, so tests can replace one
function instead of touching the network.
"""

import base64
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


def _send(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
) -> bytes:
    """Send one HTTP request and return the raw response body."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise YotoError(f"HTTP {e.code} for {method} {url}: {detail}")
    except urllib.error.URLError as e:
        raise YotoError(f"Could not reach {url}: {e.reason}")


def _request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
) -> dict:
    """Send one request and return its JSON body, or {} if there is none."""
    body = _send(method, url, headers=headers, data=data)
    try:
        decoded = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


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
    if "access_token" not in resp:
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


# --- Uploading ---------------------------------------------------------------

_POLL_INTERVAL = 1


@dataclass
class Track:
    """An audio file Yoto has transcoded and can play."""

    title: str
    sha256: str
    duration: float
    file_size: int
    channels: str | int | None
    format: str | None
    # A `yoto:#<mediaId>` icon reference, shown on the player for this track.
    icon: str | None = None


def _auth_headers(token: str, content_type: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def start_upload(path: Path, token: str) -> str:
    """Send one audio file to Yoto, which starts transcoding it. Returns the upload ID."""
    upload = _request(
        "GET", f"{API_BASE}/media/transcode/audio/uploadUrl", headers=_auth_headers(token)
    ).get("upload") or {}
    upload_url, upload_id = upload.get("uploadUrl"), upload.get("uploadId")
    if not upload_id:
        raise YotoError(f"Yoto gave no upload ID for {path.name}")
    # Yoto may already hold this exact file; then there is no URL to upload to.
    if upload_url:
        # Pre-signed URL: no Authorization header.
        _request(
            "PUT",
            upload_url,
            headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "audio/mpeg"},
            data=path.read_bytes(),
        )
    return upload_id


def wait_transcoded(
    upload_id: str,
    get_token: Callable[[], str],
    *,
    title: str,
    icon: str | None = None,
    timeout: float = 120,
    sleep=time.sleep,
    now=time.monotonic,
) -> Track:
    """Poll until Yoto has transcoded an upload, and return it as a Track.

    `get_token` is called on every poll: processing can outlive one access token.
    """
    poll_url = f"{API_BASE}/media/upload/{upload_id}/transcoded?loudnorm=false"
    deadline = now() + timeout
    while True:
        reply = _request("GET", poll_url, headers=_auth_headers(get_token()))
        transcode = reply.get("transcode") or {}
        if transcode.get("transcodedSha256"):
            info = transcode.get("transcodedInfo") or {}
            return Track(
                title=title,
                sha256=transcode["transcodedSha256"],
                duration=info.get("duration") or 0,
                file_size=info.get("fileSize") or 0,
                channels=info.get("channels"),
                format=info.get("format"),
                icon=icon,
            )
        if now() >= deadline:
            raise YotoError(f"Yoto didn't finish processing {title} within {timeout:.0f}s")
        sleep(_POLL_INTERVAL)


def build_content(title: str, tracks: list[Track], cover_url: str | None = None) -> dict:
    """The POST /content body for a playlist with one chapter per track."""
    chapters = []
    for i, t in enumerate(tracks, 1):
        track = {
            "key": "01",
            "title": t.title,
            "trackUrl": f"yoto:#{t.sha256}",
            "duration": t.duration,
            "fileSize": t.file_size,
            "type": "audio",
            "overlayLabel": str(i),
        }
        if t.channels is not None:
            track["channels"] = t.channels
        if t.format:
            track["format"] = t.format
        chapter = {"key": f"{i:02d}", "title": t.title, "overlayLabel": str(i), "tracks": [track]}
        if t.icon:
            chapter["display"] = {"icon16x16": t.icon}
            track["display"] = {"icon16x16": t.icon}
        chapters.append(chapter)

    total_size = sum(t.file_size for t in tracks)
    body = {
        "title": title,
        "content": {"chapters": chapters},
        "metadata": {
            "media": {
                "duration": sum(t.duration for t in tracks),
                "fileSize": total_size,
                "readableFileSize": round(total_size / 1024 / 1024, 1),
            }
        },
    }
    if cover_url:
        body["metadata"]["cover"] = {"imageL": cover_url}
    return body


def create_playlist(
    title: str, tracks: list[Track], token: str, cover_url: str | None = None
) -> str:
    """Create a new playlist and return its card ID.

    Always creates; never updates. Sending a cardId would replace that
    playlist's whole track list, silently dropping songs already on it.
    """
    resp = _request(
        "POST",
        f"{API_BASE}/content",
        headers=_auth_headers(token, "application/json"),
        data=json.dumps(build_content(title, tracks, cover_url)).encode(),
    )
    new_id = (resp.get("card") or {}).get("cardId")
    if not new_id:
        raise YotoError(f"Yoto didn't return a card ID: {resp}")
    return new_id


@dataclass
class Item:
    """One file to put in a playlist."""

    path: Path
    title: str | None = None  # defaults to the file name without its extension
    icon: str | None = None  # icon spec, see resolve_icons

    @property
    def name(self) -> str:
        return self.title or self.path.stem


def publish_playlist(
    title: str,
    items: list[Item],
    get_token: Callable[[], str],
    *,
    cover: str | None = None,
    progress: Callable[[str], object] = print,
) -> str:
    """Upload files in order as one new playlist and return its card ID.

    Icons and the cover come first, so a mistyped icon name fails before any
    audio is sent. Every file is sent before any is waited on, so Yoto
    transcodes them side by side. No playlist exists unless every step worked.
    `get_token` is called per request, so a long run outlives one access token.
    """
    specs = sorted({item.icon for item in items if item.icon})
    icons = {}
    if specs:
        progress("Finding icons...")
        icons = resolve_icons(specs, get_token())
    cover_url = None
    if cover:
        progress("Uploading cover image...")
        cover_url = upload_cover(cover, get_token())

    upload_ids = []
    for n, item in enumerate(items, 1):
        progress(f"[{n}/{len(items)}] Uploading {item.name}...")
        upload_ids.append(start_upload(item.path, get_token()))
    progress("Waiting for Yoto to process the audio...")
    tracks = [
        wait_transcoded(uid, get_token, title=item.name, icon=icons.get(item.icon))
        for uid, item in zip(upload_ids, items)
    ]
    progress(f"Creating playlist '{title}'...")
    return create_playlist(title, tracks, get_token(), cover_url)


# --- Icons and cover images --------------------------------------------------


def public_icons(token: str) -> list[dict]:
    """Yoto's built-in 16x16 icons: dicts with mediaId, title, publicTags."""
    return _request(
        "GET", f"{API_BASE}/media/displayIcons/user/yoto", headers=_auth_headers(token)
    ).get("displayIcons") or []


def search_icons(icons: list[dict], query: str) -> list[dict]:
    """Icons matching `query`, best first: exact title, then tag, then part of a title."""
    q = query.strip().lower()
    no_match = 3

    def rank(icon: dict) -> int:
        title = (icon.get("title") or "").lower()
        if title == q:
            return 0
        if q in (t.lower() for t in icon.get("publicTags") or []):
            return 1
        if q in title:
            return 2
        return no_match

    return sorted((i for i in icons if rank(i) < no_match), key=rank)


def resolve_icons(specs: Iterable[str], token: str) -> dict[str, str]:
    """Map each icon spec to a `yoto:#<id>` reference.

    A spec is a `yoto:#<id>` reference, a path to an image file (uploaded as a
    custom icon), or a word to search Yoto's built-in icons for. The built-in
    list is fetched only if a spec needs it, and only once.
    """
    builtin: list[dict] | None = None
    resolved = {}
    for spec in specs:
        word = spec.strip()
        path = Path(word).expanduser()
        if word.startswith("yoto:#"):
            resolved[spec] = word
        elif path.is_file():
            resolved[spec] = upload_icon(path, token)
        else:
            if builtin is None:
                builtin = public_icons(token)
            matches = search_icons(builtin, word)
            if not matches:
                raise YotoError(
                    f"No Yoto icon matches '{word}', and no such image file exists. "
                    "Find one with: yotutil yoto icons <word>"
                )
            resolved[spec] = f"yoto:#{matches[0]['mediaId']}"
    return resolved


# Leading bytes of each image format Yoto accepts.
_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF8": "image/gif",
}


def _read_image(source: str) -> tuple[bytes, str]:
    """(bytes, content type) of a local image file or an http(s) image URL.

    The type comes from the leading bytes, not the name: an MP3's embedded
    "cover.jpg" is often really a PNG.
    """
    if source.startswith(("http://", "https://")):
        data = _send("GET", source)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise YotoError(f"Image not found: {source}")
        data = path.read_bytes()
    for signature, content_type in _IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return data, content_type
    raise YotoError(f"{source} doesn't look like an image (use PNG, GIF or JPEG)")


def _post_image(endpoint: str, query: dict, source: str, token: str) -> dict:
    """POST an image as raw bytes. Not multipart: Yoto's docs show a form
    upload, but the icon endpoint rejects that with "A binary image file is
    required"."""
    data, content_type = _read_image(source)
    return _request(
        "POST",
        f"{API_BASE}/media/{endpoint}?{urllib.parse.urlencode(query)}",
        headers=_auth_headers(token, content_type),
        data=data,
    )


def upload_icon(path: Path, token: str) -> str:
    """Upload an image as a custom icon; Yoto shrinks it to 16x16. Returns its reference."""
    resp = _post_image(
        "displayIcons/user/me/upload",
        {"autoConvert": "true", "filename": path.stem},
        str(path),
        token,
    )
    media_id = (resp.get("displayIcon") or {}).get("mediaId")
    if not media_id:
        raise YotoError(f"Yoto didn't accept {path.name} as an icon: {resp}")
    return f"yoto:#{media_id}"


def upload_cover(source: str, token: str) -> str:
    """Upload a cover image (file or URL); Yoto resizes it. Returns its URL."""
    resp = _post_image(
        "coverImage/user/me/upload",
        {"autoconvert": "true", "coverType": "default"},
        source,
        token,
    )
    url = (resp.get("coverImage") or {}).get("mediaUrl")
    if not url:
        raise YotoError(f"Yoto didn't accept the cover image: {resp}")
    return url

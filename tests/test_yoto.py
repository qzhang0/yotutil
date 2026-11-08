"""Tests for the Yoto API client. No test touches the network."""

import base64
import hashlib
import json
import stat
import urllib.parse

import pytest

from yotutil import yoto


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=")
    return f"h.{payload.decode()}.s"


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "yoto_token.json"
    monkeypatch.setattr(yoto, "TOKEN_FILE", path)
    return path


@pytest.fixture
def calls(monkeypatch):
    """Record every _request call; tests set `calls.replies` to script answers."""

    class Recorder(list):
        replies: list = []

    rec = Recorder()

    def fake(method, url, *, headers=None, data=None):
        rec.append({"method": method, "url": url, "headers": headers, "data": data})
        reply = rec.replies.pop(0) if rec.replies else None
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(yoto, "_request", fake)
    return rec


# --- Sign-in -----------------------------------------------------------------


def test_pkce_challenge_is_unpadded_s256_of_verifier():
    verifier, challenge = yoto.pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected
    assert "=" not in challenge
    assert 43 <= len(verifier) <= 128


def test_authorize_url_carries_pkce_scopes_and_redirect():
    url = yoto.authorize_url("cid", "chal", "st")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["client_id"] == "cid"
    assert q["code_challenge"] == "chal"
    assert q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "http://127.0.0.1:8787/callback"
    assert q["state"] == "st"
    assert "user:content:manage" in q["scope"].split()
    assert "offline_access" in q["scope"].split()


def test_login_exchanges_code_and_saves_tokens(token_file, calls, monkeypatch):
    monkeypatch.setattr(yoto, "_wait_for_callback", lambda state: "the-code")
    calls.replies = [{"access_token": "A", "refresh_token": "R"}]

    yoto.login("cid", open_browser=lambda url: None)

    sent = dict(urllib.parse.parse_qsl(calls[0]["data"].decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["client_id"] == "cid"
    assert "code_verifier" in sent
    assert json.loads(token_file.read_text()) == {
        "access_token": "A",
        "refresh_token": "R",
    }


def test_token_file_is_private(token_file):
    yoto._save_tokens({"access_token": "A", "refresh_token": "R"})
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_access_token_without_login_says_how_to_sign_in(token_file):
    with pytest.raises(yoto.YotoError, match="yotutil yoto login"):
        yoto.access_token("cid")


def test_fresh_access_token_is_used_as_is(token_file, calls):
    token = _jwt(exp=10_000)
    token_file.write_text(json.dumps({"access_token": token, "refresh_token": "R"}))
    assert yoto.access_token("cid", now=lambda: 1_000) == token
    assert calls == []


def test_expiring_token_is_refreshed_and_new_refresh_token_saved(token_file, calls):
    token_file.write_text(
        json.dumps({"access_token": _jwt(exp=1_030), "refresh_token": "R1"})
    )
    calls.replies = [{"access_token": "A2", "refresh_token": "R2"}]

    assert yoto.access_token("cid", now=lambda: 1_000) == "A2"

    sent = dict(urllib.parse.parse_qsl(calls[0]["data"].decode()))
    assert sent == {"grant_type": "refresh_token", "client_id": "cid", "refresh_token": "R1"}
    # Refresh tokens are single-use, so the new one must replace the old.
    assert json.loads(token_file.read_text())["refresh_token"] == "R2"


def test_failed_refresh_says_how_to_sign_in(token_file, calls):
    token_file.write_text(json.dumps({"access_token": "junk", "refresh_token": "R"}))
    calls.replies = [yoto.YotoError("Yoto returned HTTP 403")]
    with pytest.raises(yoto.YotoError, match="yotutil yoto login"):
        yoto.access_token("cid")

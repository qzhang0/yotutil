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


# --- Uploading ---------------------------------------------------------------


def _done(sha="abc", duration=120, size=1000):
    return {
        "transcode": {
            "transcodedSha256": sha,
            "transcodedInfo": {
                "duration": duration,
                "fileSize": size,
                "channels": "stereo",
                "format": "aac",
            },
        }
    }


def test_upload_track_puts_file_and_polls_until_transcoded(tmp_path, calls):
    song = tmp_path / "小红帽.mp3"
    song.write_bytes(b"mp3-bytes")
    calls.replies = [
        {"upload": {"uploadUrl": "https://s3/put", "uploadId": "U1"}},
        None,  # PUT
        {"transcode": {}},  # still working
        _done(),
    ]
    slept = []

    track = yoto.upload_track(song, "TOK", sleep=slept.append)

    assert [c["method"] for c in calls] == ["GET", "PUT", "GET", "GET"]
    put = calls[1]
    assert put["url"] == "https://s3/put"
    assert put["data"] == b"mp3-bytes"
    assert put["headers"]["Content-Type"] == "audio/mpeg"
    assert "Authorization" not in put["headers"]  # pre-signed URL; don't leak the token
    assert "/media/upload/U1/transcoded" in calls[2]["url"]
    assert calls[2]["headers"]["Authorization"] == "Bearer TOK"
    assert len(slept) == 1
    assert track == yoto.Track("小红帽", "abc", 120, 1000, "stereo", "aac")


def test_upload_track_skips_put_when_yoto_already_has_the_file(tmp_path, calls):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")
    calls.replies = [{"upload": {"uploadUrl": None, "uploadId": "U1"}}, _done()]
    yoto.upload_track(song, "TOK", sleep=lambda s: None)
    assert [c["method"] for c in calls] == ["GET", "GET"]


def test_upload_track_gives_up_after_timeout(tmp_path, calls):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")
    calls.replies = [{"upload": {"uploadUrl": "u", "uploadId": "U1"}}, None] + [
        {"transcode": {}}
    ] * 10
    clock = iter(range(0, 1000, 50))
    with pytest.raises(yoto.YotoError, match="a.mp3"):
        yoto.upload_track(song, "TOK", timeout=120, sleep=lambda s: None, now=lambda: next(clock))


def test_build_content_one_chapter_per_track_in_order():
    tracks = [
        yoto.Track("小红帽", "s1", 100, 1_048_576, "stereo", "aac"),
        yoto.Track("YMCA", "s2", 200.5, 1_048_576, "stereo", "aac"),
    ]
    body = yoto.build_content("我的歌", tracks)

    assert body["title"] == "我的歌"
    assert "cardId" not in body
    chapters = body["content"]["chapters"]
    assert [c["title"] for c in chapters] == ["小红帽", "YMCA"]
    assert [c["key"] for c in chapters] == ["01", "02"]
    assert chapters[1]["tracks"][0]["trackUrl"] == "yoto:#s2"
    assert chapters[1]["tracks"][0]["type"] == "audio"
    assert body["metadata"]["media"] == {
        "duration": 300.5,
        "fileSize": 2_097_152,
        "readableFileSize": 2.0,
    }


def test_build_content_never_targets_an_existing_playlist():
    # A cardId would replace that playlist's tracks, dropping songs already on it.
    body = yoto.build_content("t", [yoto.Track("a", "s", 1, 1, None, None)])
    assert "cardId" not in body
    assert "channels" not in body["content"]["chapters"][0]["tracks"][0]


def test_upload_track_uses_given_title_over_file_name(tmp_path, calls):
    song = tmp_path / "兔小贝儿歌 484 小红帽 ｜儿歌大全.mp3"
    song.write_bytes(b"x")
    calls.replies = [{"upload": {"uploadUrl": None, "uploadId": "U1"}}, _done()]
    track = yoto.upload_track(song, "TOK", title="小红帽", sleep=lambda s: None)
    assert track.title == "小红帽"


def test_create_playlist_posts_json_and_returns_card_id(calls):
    calls.replies = [{"card": {"cardId": "NEW"}}]
    card = yoto.create_playlist("t", [yoto.Track("a", "s", 1, 1, None, None)], "TOK")
    assert card == "NEW"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/content")
    assert json.loads(calls[0]["data"])["title"] == "t"

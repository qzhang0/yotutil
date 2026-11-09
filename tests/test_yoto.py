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
        reply = rec.replies.pop(0) if rec.replies else {}
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

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"
JPEG = b"\xff\xd8\xff\xe0" + b"rest"


def _track(title="a", sha="s", icon=None, **kw) -> yoto.Track:
    fields = {"duration": 1, "file_size": 1, "channels": None, "format": None} | kw
    return yoto.Track(title, sha, icon=icon, **fields)


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


@pytest.fixture
def song(tmp_path):
    path = tmp_path / "小红帽.mp3"
    path.write_bytes(b"mp3-bytes")
    return path


def test_start_upload_puts_file_to_presigned_url(song, calls):
    calls.replies = [{"upload": {"uploadUrl": "https://s3/put", "uploadId": "U1"}}]

    assert yoto.start_upload(song, "TOK") == "U1"

    get, put = calls
    assert get["headers"]["Authorization"] == "Bearer TOK"
    assert put["method"] == "PUT"
    assert put["url"] == "https://s3/put"
    assert put["data"] == b"mp3-bytes"
    assert put["headers"]["Content-Type"] == "audio/mpeg"
    assert "Authorization" not in put["headers"]  # pre-signed URL; don't leak the token


def test_start_upload_skips_put_when_yoto_already_has_the_file(song, calls):
    calls.replies = [{"upload": {"uploadUrl": None, "uploadId": "U1"}}]
    assert yoto.start_upload(song, "TOK") == "U1"
    assert len(calls) == 1


def test_wait_transcoded_polls_until_done(calls):
    calls.replies = [{"transcode": {}}, _done()]
    slept = []

    track = yoto.wait_transcoded(
        "U1", lambda: "TOK", title="小红帽", icon="yoto:#I", sleep=slept.append
    )

    assert all("/media/upload/U1/transcoded" in c["url"] for c in calls)
    assert len(slept) == 1
    assert track == yoto.Track("小红帽", "abc", 120, 1000, "stereo", "aac", icon="yoto:#I")


def test_wait_transcoded_gives_up_after_timeout(calls):
    calls.replies = [{"transcode": {}}] * 10
    clock = iter(range(0, 1000, 50))
    with pytest.raises(yoto.YotoError, match="小红帽"):
        yoto.wait_transcoded(
            "U1",
            lambda: "TOK",
            title="小红帽",
            timeout=120,
            sleep=lambda s: None,
            now=lambda: next(clock),
        )


def test_wait_transcoded_asks_for_a_token_on_every_poll(calls):
    """A transcode can outlive one access token, so each poll gets a fresh one."""
    calls.replies = [{"transcode": {}}, _done()]
    tokens = iter(["T1", "T2"])

    yoto.wait_transcoded("U1", lambda: next(tokens), title="a", sleep=lambda s: None)

    assert [c["headers"]["Authorization"] for c in calls] == ["Bearer T1", "Bearer T2"]


def test_build_content_one_chapter_per_track_in_order():
    tracks = [
        _track("小红帽", "s1", duration=100, file_size=1_048_576, channels="stereo", format="aac"),
        _track("YMCA", "s2", duration=200.5, file_size=1_048_576),
    ]
    body = yoto.build_content("我的歌", tracks)

    assert body["title"] == "我的歌"
    chapters = body["content"]["chapters"]
    assert [c["title"] for c in chapters] == ["小红帽", "YMCA"]
    assert [c["key"] for c in chapters] == ["01", "02"]
    assert chapters[0]["tracks"][0]["channels"] == "stereo"
    assert "channels" not in chapters[1]["tracks"][0]
    assert chapters[1]["tracks"][0]["trackUrl"] == "yoto:#s2"
    assert chapters[1]["tracks"][0]["type"] == "audio"
    assert body["metadata"]["media"] == {
        "duration": 300.5,
        "fileSize": 2_097_152,
        "readableFileSize": 2.0,
    }


def test_build_content_never_targets_an_existing_playlist():
    # A cardId would replace that playlist's tracks, dropping songs already on it.
    assert "cardId" not in yoto.build_content("t", [_track()])


def test_build_content_sets_icon_on_chapter_and_track_and_cover():
    body = yoto.build_content("t", [_track(icon="yoto:#I1"), _track("b")], cover_url="https://cdn/c")

    first, second = body["content"]["chapters"]
    assert first["display"] == {"icon16x16": "yoto:#I1"}
    assert first["tracks"][0]["display"] == {"icon16x16": "yoto:#I1"}
    assert "display" not in second
    assert body["metadata"]["cover"] == {"imageL": "https://cdn/c"}


def test_build_content_without_cover_has_no_cover_key():
    assert "cover" not in yoto.build_content("t", [_track()])["metadata"]


def test_create_playlist_posts_json_and_returns_card_id(calls):
    calls.replies = [{"card": {"cardId": "NEW"}}]
    assert yoto.create_playlist("t", [_track()], "TOK") == "NEW"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/content")
    assert json.loads(calls[0]["data"])["title"] == "t"


# --- publish_playlist ----------------------------------------------------------


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the per-step API calls and log what publish_playlist does."""
    log = []
    monkeypatch.setattr(yoto, "resolve_icons", lambda specs, token: log.append(("icons", list(specs))) or {s: f"yoto:#{s}" for s in specs})
    monkeypatch.setattr(yoto, "upload_cover", lambda source, token: log.append(("cover", source)) or "https://cdn/c")
    monkeypatch.setattr(yoto, "start_upload", lambda path, token: log.append(("send", path.name)) or path.name)

    def wait(uid, get_token, *, title, icon=None):
        get_token()
        log.append(("wait", uid))
        return _track(title, icon=icon)

    monkeypatch.setattr(yoto, "wait_transcoded", wait)

    def create(title, tracks, token, cover_url=None):
        log.append(("create", title, [(t.title, t.icon) for t in tracks], cover_url))
        return "NEW"

    monkeypatch.setattr(yoto, "create_playlist", create)
    return log


def test_publish_playlist_resolves_icons_first_then_sends_all_then_waits(tmp_path, fake_api):
    items = [
        yoto.Item(tmp_path / "a.mp3", "小白船", "boat"),
        yoto.Item(tmp_path / "b.mp3", icon="boat"),
        yoto.Item(tmp_path / "c.mp3"),
    ]

    card = yoto.publish_playlist("t", items, lambda: "TOK", cover="c.jpg", progress=lambda m: None)

    assert card == "NEW"
    assert fake_api == [
        ("icons", ["boat"]),  # deduped, before any audio
        ("cover", "c.jpg"),
        ("send", "a.mp3"),  # every file sent before any wait, so Yoto
        ("send", "b.mp3"),  # transcodes them side by side
        ("send", "c.mp3"),
        ("wait", "a.mp3"),
        ("wait", "b.mp3"),
        ("wait", "c.mp3"),
        ("create", "t", [("小白船", "yoto:#boat"), ("b", "yoto:#boat"), ("c", None)], "https://cdn/c"),
    ]


def test_publish_playlist_bad_icon_stops_before_any_audio(tmp_path, fake_api, monkeypatch):
    def fail(specs, token):
        raise yoto.YotoError("No Yoto icon matches 'zzz'")

    monkeypatch.setattr(yoto, "resolve_icons", fail)
    with pytest.raises(yoto.YotoError):
        yoto.publish_playlist("t", [yoto.Item(tmp_path / "a.mp3", icon="zzz")], lambda: "TOK", progress=lambda m: None)
    assert fake_api == []


def test_publish_playlist_asks_for_a_token_per_request(tmp_path, fake_api):
    tokens = []
    yoto.publish_playlist(
        "t", [yoto.Item(tmp_path / "a.mp3"), yoto.Item(tmp_path / "b.mp3")],
        lambda: tokens.append(1) or "TOK", progress=lambda m: None,
    )
    assert len(tokens) == 5  # 2 sends + 2 waits + create: a long run outlives one token


# --- Icons and cover images --------------------------------------------------

ICONS = [
    {"mediaId": "M1", "title": "Moon boat", "publicTags": ["space"]},
    {"mediaId": "M2", "title": "Sailing", "publicTags": ["boat", "sea"]},
    {"mediaId": "M3", "title": "Boat", "publicTags": []},
    {"mediaId": "M4", "title": "Car", "publicTags": ["vehicle"]},
]


def test_search_icons_ranks_title_then_tag_then_partial_title():
    assert [i["mediaId"] for i in yoto.search_icons(ICONS, "Boat")] == ["M3", "M2", "M1"]
    assert yoto.search_icons(ICONS, "plane") == []


def test_resolve_icons_handles_each_spec_form(tmp_path, calls):
    png = tmp_path / "moon.png"
    png.write_bytes(PNG)
    calls.replies = [{"displayIcon": {"mediaId": "CUSTOM"}}, {"displayIcons": ICONS}]

    resolved = yoto.resolve_icons(["yoto:#abc", str(png), " boat ", "car"], "TOK")

    assert resolved == {
        "yoto:#abc": "yoto:#abc",
        str(png): "yoto:#CUSTOM",
        " boat ": "yoto:#M3",
        "car": "yoto:#M4",
    }
    # One icon upload, then the built-in list fetched once for both words.
    assert [c["url"].split("?")[0].rsplit("/", 1)[-1] for c in calls] == ["upload", "yoto"]


def test_resolve_icons_skips_builtin_list_when_not_needed(calls):
    yoto.resolve_icons(["yoto:#abc"], "TOK")
    assert calls == []


def test_resolve_icons_unknown_word_says_how_to_search(calls):
    calls.replies = [{"displayIcons": ICONS}]
    with pytest.raises(yoto.YotoError, match="yotutil yoto icons"):
        yoto.resolve_icons(["unicorn"], "TOK")


def test_upload_icon_sends_raw_bytes(tmp_path, calls):
    png = tmp_path / "moon.png"
    png.write_bytes(PNG)
    calls.replies = [{"displayIcon": {"mediaId": "CUSTOM"}}]

    assert yoto.upload_icon(png, "TOK") == "yoto:#CUSTOM"

    call = calls[0]
    assert "/media/displayIcons/user/me/upload?" in call["url"]
    assert "autoConvert=true" in call["url"]
    # Raw bytes: Yoto rejects the multipart upload its docs describe.
    assert call["headers"]["Content-Type"] == "image/png"
    assert call["data"] == PNG


def test_upload_icon_rejects_non_images(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("x")
    with pytest.raises(yoto.YotoError, match="image"):
        yoto.upload_icon(f, "TOK")


def test_upload_cover_sends_image_and_returns_url(tmp_path, calls):
    jpg = tmp_path / "cover.jpg"
    jpg.write_bytes(JPEG)
    calls.replies = [{"coverImage": {"mediaId": "C", "mediaUrl": "https://cdn/cover"}}]

    assert yoto.upload_cover(str(jpg), "TOK") == "https://cdn/cover"
    assert "/media/coverImage/user/me/upload?" in calls[0]["url"]
    assert "autoconvert=true" in calls[0]["url"]
    assert calls[0]["headers"]["Content-Type"] == "image/jpeg"
    assert calls[0]["data"] == JPEG


def test_image_type_comes_from_bytes_not_file_name(tmp_path, calls):
    # Embedded MP3 artwork saved as "cover.jpg" is often really a PNG.
    fake_jpg = tmp_path / "cover.jpg"
    fake_jpg.write_bytes(PNG)
    calls.replies = [{"coverImage": {"mediaUrl": "u"}}]
    yoto.upload_cover(str(fake_jpg), "TOK")
    assert calls[0]["headers"]["Content-Type"] == "image/png"


def test_upload_cover_from_url_goes_through_send(calls, monkeypatch):
    fetched = []
    monkeypatch.setattr(yoto, "_send", lambda method, url, **kw: fetched.append(url) or JPEG)
    calls.replies = [{"coverImage": {"mediaUrl": "https://cdn/cover"}}]

    assert yoto.upload_cover("https://example.com/c.jpg", "TOK") == "https://cdn/cover"
    assert fetched == ["https://example.com/c.jpg"]
    assert calls[0]["data"] == JPEG


def test_upload_cover_missing_file_is_a_clear_error():
    with pytest.raises(yoto.YotoError, match="not found"):
        yoto.upload_cover("/nope/cover.jpg", "TOK")

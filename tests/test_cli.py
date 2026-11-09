"""Tests for CLI module."""

import pytest
import typer
from typer.testing import CliRunner
from yt_dlp.utils import remove_terminal_sequences

from yotutil import yoto
from yotutil.cli import (
    _BatchLine,
    _parse_batch_file,
    _prepare,
    _validate_output_options,
    app,
)
from yotutil.downloader import DownloadError

runner = CliRunner()


def _plain(result) -> str:
    """CLI output with colour codes stripped.

    Rich colours help and error output whenever it believes the destination
    supports it — true on CI, false in a local pipe. Colouring splits strings
    like "--video" across escape sequences, so asserting against raw output
    passes locally and fails on CI.
    """
    return remove_terminal_sequences(result.output)


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "yotutil" in _plain(result).lower() or "download" in _plain(result).lower()


def test_dl_help():
    result = runner.invoke(app, ["dl", "--help"])
    assert result.exit_code == 0
    assert "--output-dir" in _plain(result)
    assert "--quality" in _plain(result)


def test_dl_help_offers_video_option():
    result = runner.invoke(app, ["dl", "--help"])
    assert result.exit_code == 0
    assert "--video" in _plain(result)


def test_batch_help_offers_video_option():
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "--video" in _plain(result)


def test_help_offers_max_height_option():
    for command in ("dl", "batch"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "--max-height" in _plain(result)


@pytest.mark.parametrize(
    "extra_args",
    [["--max-height", "720"], ["--compatible"], ["--max-height", "720", "--compatible"]],
)
def test_dl_rejects_video_options_without_video(extra_args):
    """Silently ignoring these produced an MP3 with no sign the flag did nothing."""
    result = runner.invoke(app, ["dl", "https://example.com/x", *extra_args])

    assert result.exit_code != 0
    assert "--video" in _plain(result)


def test_batch_rejects_video_options_without_video(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://example.com/x\n")

    result = runner.invoke(app, ["batch", str(urls), "--compatible"])

    assert result.exit_code != 0
    assert "--video" in _plain(result)


def test_video_options_accepted_with_video():
    """The combination that makes sense must not raise."""
    _validate_output_options(
        video=True, max_height=720, compatible=True, quality=None
    )


def test_no_video_options_is_fine_without_video():
    _validate_output_options(
        video=False, max_height=None, compatible=False, quality=None
    )


def test_validate_names_the_offending_option():
    with pytest.raises(typer.BadParameter, match="--max-height"):
        _validate_output_options(
            video=False, max_height=720, compatible=False, quality=None
        )


def test_quality_accepted_for_audio():
    _validate_output_options(
        video=False, max_height=None, compatible=False, quality="5"
    )


def test_quality_rejected_with_video():
    """--quality sets MP3 quality; no MP3 is produced in video mode."""
    with pytest.raises(typer.BadParameter, match="--quality"):
        _validate_output_options(
            video=True, max_height=None, compatible=False, quality="5"
        )


def test_dl_rejects_quality_with_video():
    result = runner.invoke(
        app, ["dl", "https://example.com/x", "--video", "--quality", "5"]
    )

    assert result.exit_code != 0
    assert "--quality" in _plain(result)


def test_config_quality_survives_when_flag_absent(tmp_path, monkeypatch):
    """Regression: the CLI default used to clobber config.toml unconditionally."""
    config_file = tmp_path / "config.toml"
    config_file.write_text('audio_quality = "5"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", config_file)

    config, _ = _prepare(
        output_dir=str(tmp_path / "out"), quality=None, verbose=False
    )

    assert config.audio_quality == "5"


def test_cli_quality_overrides_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text('audio_quality = "5"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", config_file)

    config, _ = _prepare(output_dir=str(tmp_path / "out"), quality="9", verbose=False)

    assert config.audio_quality == "9"


def test_batch_file_not_found():
    result = runner.invoke(app, ["batch", "/nonexistent/urls.txt"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "Error" in result.output


def test_batch_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("\n\n# comment\n")
    result = runner.invoke(app, ["batch", str(f)])
    assert result.exit_code == 1
    assert "No URLs" in result.output


def test_yoto_login_without_client_id_explains_setup(tmp_path, monkeypatch):
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", tmp_path / "none.toml")
    result = runner.invoke(app, ["yoto", "login"])
    assert result.exit_code == 1
    assert "dashboard.yoto.dev" in _plain(result)
    assert "yoto_client_id" in _plain(result)


def test_parse_batch_file_reads_optional_track_names_and_icons():
    text = (
        "# kids\n"
        "https://youtu.be/a 小红帽\n"
        "\n"
        "  https://youtu.be/b\n"
        "https://youtu.be/c\t让我们 荡起双桨 \n"
        "https://youtu.be/d 小白船 | boat\n"
        "https://youtu.be/e | ~/icons/moon.png\n"
    )
    assert _parse_batch_file(text) == [
        _BatchLine("https://youtu.be/a", "小红帽"),
        _BatchLine("https://youtu.be/b"),
        _BatchLine("https://youtu.be/c", "让我们 荡起双桨"),
        _BatchLine("https://youtu.be/d", "小白船", "boat"),
        _BatchLine("https://youtu.be/e", None, "~/icons/moon.png"),
    ]


@pytest.fixture
def yoto_env(tmp_path, monkeypatch):
    """Signed in to Yoto, with publish_playlist and download() replaced by fakes."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('yoto_client_id = "cid"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", cfg)
    monkeypatch.setattr(yoto, "access_token", lambda cid: "TOK")
    monkeypatch.setattr(
        yoto,
        "public_icons",
        lambda token: [{"mediaId": "BOAT", "title": "Boat", "publicTags": ["boat"]}],
    )

    downloads = {
        "https://youtu.be/a": [tmp_path / "Long YouTube title A.mp3"],
        "https://youtu.be/list": [tmp_path / "x.mp3", tmp_path / "y.mp3"],
    }

    def fake_download(url, config, **kw):
        if url not in downloads:
            raise DownloadError("gone")
        return downloads[url]

    monkeypatch.setattr("yotutil.cli.download", fake_download)

    published = []

    def fake_publish(title, items, get_token, *, cover=None, progress=print):
        published.append(
            {"title": title, "items": [(i.path.name, i.title, i.icon) for i in items], "cover": cover}
        )
        return "NEW"

    monkeypatch.setattr(yoto, "publish_playlist", fake_publish)
    return tmp_path, published


def _songs(tmp_path, text):
    f = tmp_path / "songs.txt"
    f.write_text(text)
    return str(f)


def test_yoto_upload_passes_files_in_given_order_with_icon_and_cover(yoto_env):
    tmp_path, published = yoto_env
    files = [tmp_path / n for n in ("z.mp3", "a.mp3")]
    for f in files:
        f.write_bytes(b"x")

    result = runner.invoke(
        app, ["yoto", "upload", *map(str, files), "--title", "t", "--icon", "boat", "--cover", "c.jpg"]
    )

    assert result.exit_code == 0, result.output
    assert published == [
        {"title": "t", "items": [("z.mp3", None, "boat"), ("a.mp3", None, "boat")], "cover": "c.jpg"}
    ]
    assert "NEW" in _plain(result)


def test_yoto_upload_failure_says_no_playlist_was_created(yoto_env, monkeypatch):
    tmp_path, _ = yoto_env
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")

    def fail(*a, **k):
        raise yoto.YotoError("boom")

    monkeypatch.setattr(yoto, "publish_playlist", fail)
    result = runner.invoke(app, ["yoto", "upload", str(song), "--title", "t"])

    assert result.exit_code == 1
    assert "boom" in _plain(result)
    assert "No playlist was created" in _plain(result)


def test_batch_yoto_builds_items_from_lines(yoto_env):
    tmp_path, published = yoto_env
    songs = _songs(tmp_path, "https://youtu.be/a 小白船 | boat\nhttps://youtu.be/list 不用\n")

    result = runner.invoke(
        app, ["batch", songs, "-o", str(tmp_path), "--yoto", "儿歌精选3", "--icon", "music", "--cover", "c.jpg"]
    )

    assert result.exit_code == 0, result.output
    assert published == [
        {
            "title": "儿歌精选3",
            "items": [
                ("Long YouTube title A.mp3", "小白船", "boat"),  # line icon wins
                # A playlist URL keeps its file names; the --icon default fills in.
                ("x.mp3", None, "music"),
                ("y.mp3", None, "music"),
            ],
            "cover": "c.jpg",
        }
    ]


def test_batch_yoto_uploads_nothing_when_a_download_fails(yoto_env):
    tmp_path, published = yoto_env
    songs = _songs(tmp_path, "https://youtu.be/a\nhttps://youtu.be/missing\n")

    result = runner.invoke(app, ["batch", songs, "-o", str(tmp_path), "--yoto", "t"])

    assert result.exit_code == 1
    assert published == []
    assert "Nothing was uploaded to Yoto" in _plain(result)


def test_batch_yoto_checks_sign_in_before_downloading(yoto_env, monkeypatch):
    tmp_path, published = yoto_env
    downloaded = []
    monkeypatch.setattr("yotutil.cli.download", lambda url, config, **kw: downloaded.append(url))

    def not_signed_in(cid):
        raise yoto.YotoError("Not signed in to Yoto. Run: yotutil yoto login")

    monkeypatch.setattr(yoto, "access_token", not_signed_in)
    result = runner.invoke(app, ["batch", _songs(tmp_path, "https://youtu.be/a\n"), "--yoto", "t"])

    assert result.exit_code == 1
    assert downloaded == []
    assert "yotutil yoto login" in _plain(result)


def test_batch_yoto_rejects_video(tmp_path):
    result = runner.invoke(app, ["batch", _songs(tmp_path, "https://youtu.be/a\n"), "--video", "--yoto", "t"])
    assert result.exit_code != 0
    assert "--yoto" in _plain(result)


def test_batch_icon_and_cover_need_yoto(tmp_path):
    result = runner.invoke(app, ["batch", _songs(tmp_path, "https://youtu.be/a\n"), "--cover", "c.jpg"])
    assert result.exit_code != 0
    assert "--yoto" in _plain(result)


def test_yoto_icons_searches_builtin_icons(yoto_env):
    result = runner.invoke(app, ["yoto", "icons", "boat"])
    assert result.exit_code == 0, result.output
    assert "Boat" in _plain(result)

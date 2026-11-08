"""Tests for CLI module."""

import pytest
import typer
from typer.testing import CliRunner
from yt_dlp.utils import remove_terminal_sequences

from yotutil.cli import _prepare, _validate_output_options, app
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


def test_yoto_upload_creates_no_playlist_when_a_file_fails(tmp_path, monkeypatch):
    from yotutil import yoto

    cfg = tmp_path / "config.toml"
    cfg.write_text('yoto_client_id = "cid"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", cfg)
    monkeypatch.setattr(yoto, "access_token", lambda cid: "TOK")
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    def fake_upload(path, token, title=None):
        if path.name == "b.mp3":
            raise yoto.YotoError("boom")
        return yoto.Track(path.stem, "s", 1, 1, None, None)

    created = []
    monkeypatch.setattr(yoto, "upload_track", fake_upload)
    monkeypatch.setattr(yoto, "create_playlist", lambda *a, **k: created.append(a))

    result = runner.invoke(app, ["yoto", "upload", str(a), str(b), "--title", "t"])

    assert result.exit_code == 1
    assert created == []
    assert "No playlist was created" in _plain(result)


def test_yoto_upload_keeps_given_file_order(tmp_path, monkeypatch):
    from yotutil import yoto

    cfg = tmp_path / "config.toml"
    cfg.write_text('yoto_client_id = "cid"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", cfg)
    monkeypatch.setattr(yoto, "access_token", lambda cid: "TOK")
    files = [tmp_path / n for n in ("z.mp3", "a.mp3", "m.mp3")]
    for f in files:
        f.write_bytes(b"x")
    monkeypatch.setattr(
        yoto,
        "upload_track",
        lambda p, t, title=None: yoto.Track(title or p.stem, "s", 1, 1, None, None),
    )
    created = {}

    def fake_create(title, tracks, token):
        created.update(title=title, names=[t.title for t in tracks])
        return "NEW"

    monkeypatch.setattr(yoto, "create_playlist", fake_create)

    result = runner.invoke(app, ["yoto", "upload", *map(str, files), "--title", "t"])

    assert result.exit_code == 0, result.output
    assert created == {"title": "t", "names": ["z", "a", "m"]}
    assert "NEW" in _plain(result)


def test_yoto_upload_has_no_option_to_overwrite_a_playlist():
    result = runner.invoke(app, ["yoto", "upload", "--help"])
    assert "--card-id" not in _plain(result)


def test_parse_batch_file_reads_optional_track_names():
    from yotutil.cli import _parse_batch_file

    text = "# kids\nhttps://youtu.be/a 小红帽\n\n  https://youtu.be/b\nhttps://youtu.be/c\t让我们 荡起双桨 \n"
    assert _parse_batch_file(text) == [
        ("https://youtu.be/a", "小红帽"),
        ("https://youtu.be/b", None),
        ("https://youtu.be/c", "让我们 荡起双桨"),
    ]


@pytest.fixture
def yoto_batch(tmp_path, monkeypatch):
    """A batch run with download() and the Yoto calls replaced by fakes."""
    from yotutil import yoto

    cfg = tmp_path / "config.toml"
    cfg.write_text('yoto_client_id = "cid"\n')
    monkeypatch.setattr("yotutil.config.CONFIG_FILE", cfg)
    monkeypatch.setattr(yoto, "access_token", lambda cid: "TOK")
    downloads = {
        "https://youtu.be/a": [str(tmp_path / "Long YouTube title A.mp3")],
        "https://youtu.be/list": [str(tmp_path / "x.mp3"), str(tmp_path / "y.mp3")],
    }

    def fake_download(url, config, **kw):
        if url not in downloads:
            raise DownloadError("gone")
        return downloads[url]

    monkeypatch.setattr("yotutil.cli.download", fake_download)
    uploaded, created = [], []
    monkeypatch.setattr(
        yoto,
        "upload_track",
        lambda p, t, title=None: uploaded.append(title)
        or yoto.Track(title, "s", 1, 1, None, None),
    )
    monkeypatch.setattr(
        yoto, "create_playlist", lambda title, tracks, token: created.append(title) or "NEW"
    )
    return tmp_path, uploaded, created


def test_batch_yoto_uploads_named_tracks_in_order(yoto_batch):
    tmp_path, uploaded, created = yoto_batch
    f = tmp_path / "songs.txt"
    f.write_text("https://youtu.be/a 小红帽\nhttps://youtu.be/list 不用\n")

    result = runner.invoke(app, ["batch", str(f), "-o", str(tmp_path), "--yoto", "儿歌精选3"])

    assert result.exit_code == 0, result.output
    # The name applies to the single song; a playlist URL keeps its file names.
    assert uploaded == ["小红帽", "x", "y"]
    assert created == ["儿歌精选3"]


def test_batch_yoto_uploads_nothing_when_a_download_fails(yoto_batch):
    tmp_path, uploaded, created = yoto_batch
    f = tmp_path / "songs.txt"
    f.write_text("https://youtu.be/a\nhttps://youtu.be/missing\n")

    result = runner.invoke(app, ["batch", str(f), "-o", str(tmp_path), "--yoto", "t"])

    assert result.exit_code == 1
    assert uploaded == [] and created == []
    assert "Nothing was uploaded to Yoto" in _plain(result)


def test_batch_yoto_rejects_video(tmp_path):
    f = tmp_path / "songs.txt"
    f.write_text("https://youtu.be/a\n")
    result = runner.invoke(app, ["batch", str(f), "--video", "--yoto", "t"])
    assert result.exit_code != 0
    assert "--yoto" in _plain(result)

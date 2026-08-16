"""Tests for CLI module."""

import pytest
import typer
from typer.testing import CliRunner
from yt_dlp.utils import remove_terminal_sequences

from yotutil.cli import _prepare, _validate_output_options, app

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

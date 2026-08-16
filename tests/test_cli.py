"""Tests for CLI module."""

import pytest
import typer
from typer.testing import CliRunner

from yotutil.cli import _validate_video_options, app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "yotutil" in result.output.lower() or "download" in result.output.lower()


def test_dl_help():
    result = runner.invoke(app, ["dl", "--help"])
    assert result.exit_code == 0
    assert "--output-dir" in result.output
    assert "--quality" in result.output


def test_dl_help_offers_video_option():
    result = runner.invoke(app, ["dl", "--help"])
    assert result.exit_code == 0
    assert "--video" in result.output


def test_batch_help_offers_video_option():
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "--video" in result.output


def test_help_offers_max_height_option():
    for command in ("dl", "batch"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "--max-height" in result.output


@pytest.mark.parametrize(
    "extra_args",
    [["--max-height", "720"], ["--compatible"], ["--max-height", "720", "--compatible"]],
)
def test_dl_rejects_video_options_without_video(extra_args):
    """Silently ignoring these produced an MP3 with no sign the flag did nothing."""
    result = runner.invoke(app, ["dl", "https://example.com/x", *extra_args])

    assert result.exit_code != 0
    assert "--video" in result.output


def test_batch_rejects_video_options_without_video(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://example.com/x\n")

    result = runner.invoke(app, ["batch", str(urls), "--compatible"])

    assert result.exit_code != 0
    assert "--video" in result.output


def test_video_options_accepted_with_video():
    """The combination that makes sense must not raise."""
    _validate_video_options(video=True, max_height=720, compatible=True)


def test_no_video_options_is_fine_without_video():
    _validate_video_options(video=False, max_height=None, compatible=False)


def test_validate_names_the_offending_option():
    with pytest.raises(typer.BadParameter, match="--max-height"):
        _validate_video_options(video=False, max_height=720, compatible=False)


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

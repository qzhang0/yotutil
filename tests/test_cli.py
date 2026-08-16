"""Tests for CLI module."""

from typer.testing import CliRunner

from yotutil.cli import app

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

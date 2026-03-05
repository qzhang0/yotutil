"""Tests for downloader module."""

from yotutil.config import Config
from yotutil.downloader import build_yt_dlp_opts


def test_build_opts_default():
    config = Config()
    opts = build_yt_dlp_opts(config, output_dir="/tmp/out")

    assert opts["format"] == "bestaudio/best"
    assert opts["outtmpl"] == "/tmp/out/%(title)s.%(ext)s"

    # Should have audio extraction, metadata, and thumbnail postprocessors
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert "FFmpegMetadata" in keys
    assert "EmbedThumbnail" in keys
    assert opts["writethumbnail"] is True


def test_build_opts_no_metadata():
    config = Config(embed_metadata=False, embed_thumbnail=False)
    opts = build_yt_dlp_opts(config)

    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert "FFmpegMetadata" not in keys
    assert "EmbedThumbnail" not in keys
    assert "writethumbnail" not in opts


def test_build_opts_quality():
    config = Config(audio_quality="5")
    opts = build_yt_dlp_opts(config)

    audio_pp = next(pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
    assert audio_pp["preferredquality"] == "5"

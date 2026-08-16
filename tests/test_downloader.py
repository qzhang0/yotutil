"""Tests for downloader module."""

from unittest.mock import patch

import pytest
import yt_dlp

from yotutil.config import Config
from yotutil.downloader import (
    DownloadError,
    _looks_like_stale_extractor,
    build_yt_dlp_opts,
    download,
)


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


def test_build_opts_pins_working_player_clients():
    """YouTube 403s / SABR-blocks yt-dlp's default clients; we pin known-good ones."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out")

    clients = opts["extractor_args"]["youtube"]["player_client"]
    assert clients[0] == "web_embedded"
    assert "default" in clients  # fallback retained


def test_build_opts_player_clients_overridable_from_config():
    """Users must be able to route around the next YouTube breakage themselves."""
    config = Config(player_clients=["ios", "web"])
    opts = build_yt_dlp_opts(config, output_dir="/tmp/out")

    assert opts["extractor_args"]["youtube"]["player_client"] == ["ios", "web"]


def test_build_opts_suppresses_yt_dlp_progress():
    """Our own progress hook renders progress; yt-dlp's bar would interleave."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out")
    assert opts["noprogress"] is True


def test_build_opts_video_keeps_original_video():
    """--video skips the MP3 transcode and merges picture + sound into one file."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out", video=True)

    assert opts["format"] == "bestvideo*+bestaudio/best"
    assert opts["merge_output_format"] == "mp4"

    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" not in keys


def test_build_opts_video_respects_max_height():
    """Capping height avoids multi-GB 4K pulls for long videos."""
    opts = build_yt_dlp_opts(
        Config(), output_dir="/tmp/out", video=True, max_height=720
    )
    assert opts["format"] == "bestvideo[height<=720]+bestaudio/best[height<=720]"


def test_build_opts_max_height_ignored_without_video():
    """Height is meaningless when only audio is being fetched."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out", max_height=720)
    assert opts["format"] == "bestaudio/best"


def test_build_opts_video_still_embeds_metadata_and_thumbnail():
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out", video=True)

    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegMetadata" in keys
    assert "EmbedThumbnail" in keys


def test_build_opts_audio_path_unchanged_by_video_flag():
    """Regression: the default MP3 path must be untouched by the new option."""
    assert build_yt_dlp_opts(Config(), output_dir="/tmp/out") == build_yt_dlp_opts(
        Config(), output_dir="/tmp/out", video=False
    )


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


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [youtube] abc: This video is not available",
        "ERROR: Unable to extract player response",
        "nsig extraction failed",
    ],
)
def test_looks_like_stale_extractor_true(message):
    assert _looks_like_stale_extractor(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: ffmpeg not found",
        "HTTP Error 403: Forbidden",
        "Postprocessing: error converting to mp3",
    ],
)
def test_looks_like_stale_extractor_false(message):
    assert _looks_like_stale_extractor(message) is False


def _patch_download_raising(message):
    """Make yt_dlp.YoutubeDL(...).download(...) raise DownloadError(message)."""
    mock_ydl = patch("yotutil.downloader.yt_dlp.YoutubeDL").start()
    instance = mock_ydl.return_value.__enter__.return_value
    instance.download.side_effect = yt_dlp.utils.DownloadError(message)
    return mock_ydl


@patch("yotutil.downloader.check_ffmpeg")
def test_download_appends_hint_on_stale_extractor(_mock_ffmpeg):
    _patch_download_raising("ERROR: [youtube] abc: This video is not available")
    try:
        with pytest.raises(DownloadError) as exc:
            download("https://youtu.be/abc", Config(), output_dir="/tmp/out")
    finally:
        patch.stopall()

    assert "out of date" in str(exc.value)
    # Both the installed-tool and checkout upgrade paths are offered.
    assert "uv tool upgrade yotutil" in str(exc.value)
    assert "uv sync --upgrade-package yt-dlp" in str(exc.value)


@patch("yotutil.downloader.check_ffmpeg")
def test_download_no_hint_on_unrelated_failure(_mock_ffmpeg):
    _patch_download_raising("ERROR: Postprocessing: error converting to mp3")
    try:
        with pytest.raises(DownloadError) as exc:
            download("https://youtu.be/abc", Config(), output_dir="/tmp/out")
    finally:
        patch.stopall()

    assert "out of date" not in str(exc.value)

"""Tests for downloader module."""

from unittest.mock import patch

import pytest
import yt_dlp

from yotutil.config import Config
from yotutil.downloader import (
    DownloadError,
    _looks_like_stale_extractor,
    _PostprocessorReporter,
    _progress_hook,
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


@pytest.mark.parametrize(
    "video, max_height, compatible, expected",
    [
        # Audio mode ignores both video-only knobs — streams have neither a
        # frame height nor a video codec.
        (False, None, False, "bestaudio/best"),
        (False, 720, False, "bestaudio/best"),
        (False, None, True, "bestaudio/best"),
        (True, None, False, "bestvideo*+bestaudio/best"),
        # Capping height avoids multi-GB 4K pulls for long videos.
        (True, 720, False, "bestvideo[height<=720]+bestaudio/best[height<=720]"),
        # QuickTime can't open AV1; prefer avc1/mp4a, but still fall back to
        # anything playable when YouTube offers no H.264.
        (
            True,
            None,
            True,
            "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo+bestaudio/best",
        ),
        (
            True,
            720,
            True,
            "bestvideo[vcodec^=avc1][height<=720]+bestaudio[acodec^=mp4a]/"
            "bestvideo[height<=720]+bestaudio/best",
        ),
    ],
    ids=[
        "audio",
        "audio-ignores-height",
        "audio-ignores-compatible",
        "video-best",
        "video-capped",
        "video-compatible",
        "video-compatible-capped",
    ],
)
def test_build_opts_format_selector(video, max_height, compatible, expected):
    opts = build_yt_dlp_opts(
        Config(),
        output_dir="/tmp/out",
        video=video,
        max_height=max_height,
        compatible=compatible,
    )
    assert opts["format"] == expected


def test_build_opts_video_muxes_and_skips_transcode():
    """--video merges picture + sound into one file with no MP3 conversion."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out", video=True)

    assert opts["merge_output_format"] == "mp4"
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" not in keys


def test_build_opts_registers_postprocessor_hook():
    """Post-download stages run silently otherwise, which reads as a hang."""
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out")
    assert opts["postprocessor_hooks"]


def test_postprocessor_reporter_announces_slow_stages(capsys):
    reporter = _PostprocessorReporter()
    reporter({"status": "started", "postprocessor": "Merger"})
    assert "Merging" in capsys.readouterr().out


def test_postprocessor_reporter_ignores_finished_events(capsys):
    reporter = _PostprocessorReporter()
    reporter({"status": "finished", "postprocessor": "Merger"})
    assert capsys.readouterr().out == ""


def test_postprocessor_reporter_deduplicates_repeats(capsys):
    """yt-dlp fires several stages twice; announcing twice looks broken."""
    reporter = _PostprocessorReporter()
    reporter({"status": "started", "postprocessor": "Metadata"})
    reporter({"status": "started", "postprocessor": "Metadata"})
    assert capsys.readouterr().out.count("metadata") == 1


def test_build_opts_video_still_embeds_metadata_and_thumbnail():
    opts = build_yt_dlp_opts(Config(), output_dir="/tmp/out", video=True)

    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegMetadata" in keys
    assert "EmbedThumbnail" in keys


def test_build_opts_audio_path_unchanged_by_video_flag():
    """Regression: the default MP3 path must be untouched by the new options."""
    default = build_yt_dlp_opts(Config(), output_dir="/tmp/out")
    explicit = build_yt_dlp_opts(
        Config(), output_dir="/tmp/out", video=False, max_height=None, compatible=False
    )

    # Each call builds its own reporter (dedupe state must not leak between
    # downloads), so those instances never compare equal. Check them by count
    # and compare everything else.
    for opts in (default, explicit):
        assert len(opts.pop("postprocessor_hooks")) == 1

    assert default == explicit


def test_progress_hook_strips_ansi_colour_codes(capsys):
    """yt-dlp colours these in a TTY; the escapes must not reach our line."""
    _progress_hook(
        {
            "status": "downloading",
            "_percent_str": "\x1b[0;94m100.0%\x1b[0m",
            "_speed_str": "\x1b[0;32m  10.27MiB/s\x1b[0m",
            "_eta_str": "\x1b[0;33m00:00\x1b[0m",
        }
    )
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "100.0% at 10.27MiB/s ETA 00:00" in out


def test_progress_hook_pads_so_short_lines_erase_long_ones(capsys):
    """A shorter line must fully overwrite a longer one, or fragments linger."""
    _progress_hook(
        {
            "status": "downloading",
            "_percent_str": "100.0%",
            "_speed_str": "Unknown B/s",
            "_eta_str": "Unknown",
        }
    )
    long_line = capsys.readouterr().out

    _progress_hook({"status": "finished"})
    finish_line = capsys.readouterr().out

    assert len(finish_line.rstrip("\n")) >= len(long_line)


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
        # A 403 on the media URL means the chosen player client fell out of
        # favour — the remedy (update, or change player_clients) is the same.
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    ],
)
def test_looks_like_stale_extractor_true(message):
    assert _looks_like_stale_extractor(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: ffmpeg not found",
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

"""yt-dlp wrapper for downloading and converting to MP3."""

import logging
import shutil

import yt_dlp

from yotutil.config import Config

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when a download fails."""


def check_ffmpeg() -> None:
    """Raise if ffmpeg is not installed."""
    if shutil.which("ffmpeg") is None:
        raise DownloadError(
            "ffmpeg is required but not found. Install it with: brew install ffmpeg"
        )


def _detect_js_runtimes() -> dict:
    """Detect available JS runtimes for yt-dlp YouTube extraction."""
    runtimes = {}
    for name in ("deno", "node", "bun"):
        if shutil.which(name) is not None:
            runtimes[name] = {}
    if not runtimes:
        raise DownloadError(
            "A JavaScript runtime (deno, node, or bun) is required for YouTube downloads. "
            "Install one with: brew install node"
        )
    return runtimes


# Extraction failures that usually mean yt-dlp itself is out of date rather than
# the video being genuinely gone (YouTube changes break old extractors).
_STALE_EXTRACTOR_SIGNS = (
    "video is not available",
    "video unavailable",
    "failed to extract",
    "unable to extract",
    "nsig extraction failed",
    "sign in to confirm",
)


def _looks_like_stale_extractor(message: str) -> bool:
    """Heuristic: does this error look like an outdated-yt-dlp extraction failure?"""
    msg = message.lower()
    return any(sign in msg for sign in _STALE_EXTRACTOR_SIGNS)


def _progress_hook(d: dict) -> None:
    """Display download progress."""
    if d["status"] == "downloading":
        pct = d.get("_percent_str", "?%").strip()
        speed = d.get("_speed_str", "?").strip()
        eta = d.get("_eta_str", "?").strip()
        print(f"\r  {pct} at {speed} ETA {eta}", end="", flush=True)
    elif d["status"] == "finished":
        print(f"\r  Download complete, converting...", flush=True)


def build_yt_dlp_opts(config: Config, output_dir: str | None = None) -> dict:
    """Build yt-dlp options dict from config."""
    out = output_dir or config.output_dir

    opts: dict = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": config.audio_quality,
            },
        ],
        "outtmpl": f"{out}/%(title)s.%(ext)s",
        "progress_hooks": [_progress_hook],
        "quiet": not logger.isEnabledFor(logging.DEBUG),
        "no_warnings": not logger.isEnabledFor(logging.DEBUG),
        # yt-dlp requires a JS runtime + EJS solver for YouTube extraction
        "js_runtimes": _detect_js_runtimes(),
        "remote_components": {"ejs:github": {}},
    }

    if config.embed_metadata:
        opts["postprocessors"].append({"key": "FFmpegMetadata"})

    if config.embed_thumbnail:
        opts["postprocessors"].append({"key": "EmbedThumbnail"})
        opts["writethumbnail"] = True

    return opts


def download(url: str, config: Config, output_dir: str | None = None) -> None:
    """Download a single URL (video or playlist) and convert to MP3."""
    check_ffmpeg()

    opts = build_yt_dlp_opts(config, output_dir)
    logger.debug("yt-dlp options: %s", opts)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        msg = f"Download failed: {e}"
        if _looks_like_stale_extractor(str(e)):
            msg += (
                " — if the video plays in a browser, your yt-dlp may be out of "
                "date; update with: uv sync --upgrade-package yt-dlp"
            )
        raise DownloadError(msg) from e

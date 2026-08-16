"""yt-dlp wrapper for downloading and converting to MP3."""

import logging
import shutil
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _available_js_runtimes() -> tuple[str, ...]:
    """Which JS runtimes are on PATH. Cached: PATH won't change mid-run."""
    return tuple(
        name for name in ("deno", "node", "bun") if shutil.which(name) is not None
    )


def _detect_js_runtimes() -> dict:
    """Build yt-dlp's js_runtimes option from the runtimes available on PATH."""
    runtimes = _available_js_runtimes()
    if not runtimes:
        raise DownloadError(
            "A JavaScript runtime (deno, node, or bun) is required for YouTube downloads. "
            "Install one with: brew install node"
        )
    # Fresh dict per call — yt-dlp takes ownership of what we hand it.
    return {name: {} for name in runtimes}


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


# YouTube serves different format sets to different player clients, and the ones
# yt-dlp picks by default currently fail: `android_vr` returns media URLs that
# 403, `tv` yields SABR-only formats with no URL, and `web`/`ios`/`mweb` require a
# GVS PO token we don't have. `web_embedded` still returns plain HTTPS audio
# formats, so try it first and leave the rest as fallbacks for when that changes.
# This list WILL rot as YouTube changes; `player_clients` in config.toml lets a
# user work around the next breakage without waiting for a release.
DEFAULT_PLAYER_CLIENTS = ["web_embedded", "tv", "android_vr", "default"]


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
        # "processing", not "converting" — with --video nothing is transcoded.
        print("\r  Download complete, processing...", flush=True)


def _video_format(max_height: int | None) -> str:
    """Format selector for video mode, optionally capped by frame height."""
    if max_height is None:
        return "bestvideo*+bestaudio/best"
    # Uncapped, a long 4K video can run to tens of GB. This caps size only —
    # YouTube often serves AV1 at every height, so it does not guarantee a
    # more widely-playable codec.
    return f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"


def build_yt_dlp_opts(
    config: Config,
    output_dir: str | None = None,
    video: bool = False,
    max_height: int | None = None,
) -> dict:
    """Build yt-dlp options dict from config.

    With `video`, keep the original video instead of extracting audio: pull the
    best video+audio streams and mux them, skipping the MP3 transcode entirely.
    `config.audio_quality` is an MP3 setting and has no effect in this mode, and
    `max_height` only applies there — audio streams have no frame height.
    """
    out = output_dir or config.output_dir

    opts: dict = {
        "format": _video_format(max_height) if video else "bestaudio/best",
        "postprocessors": [],
        "outtmpl": f"{out}/%(title)s.%(ext)s",
        "progress_hooks": [_progress_hook],
        "quiet": not logger.isEnabledFor(logging.DEBUG),
        "no_warnings": not logger.isEnabledFor(logging.DEBUG),
        # We render our own progress via _progress_hook; without this yt-dlp
        # prints its bar too and the two interleave on the same line.
        "noprogress": True,
        # yt-dlp requires a JS runtime + EJS solver for YouTube extraction
        "js_runtimes": _detect_js_runtimes(),
        "remote_components": {"ejs:github": {}},
        "extractor_args": {
            "youtube": {"player_client": config.player_clients or DEFAULT_PLAYER_CLIENTS}
        },
    }

    if video:
        # Picture and sound arrive as separate streams; mux them into one file.
        opts["merge_output_format"] = "mp4"
    else:
        opts["postprocessors"].append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": config.audio_quality,
            }
        )

    if config.embed_metadata:
        opts["postprocessors"].append({"key": "FFmpegMetadata"})

    if config.embed_thumbnail:
        opts["postprocessors"].append({"key": "EmbedThumbnail"})
        opts["writethumbnail"] = True

    return opts


def download(
    url: str,
    config: Config,
    output_dir: str | None = None,
    video: bool = False,
    max_height: int | None = None,
) -> None:
    """Download a single URL (video or playlist), as MP3 unless `video` is set."""
    check_ffmpeg()

    opts = build_yt_dlp_opts(config, output_dir, video=video, max_height=max_height)
    logger.debug("yt-dlp options: %s", opts)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        msg = f"Download failed: {e}"
        if _looks_like_stale_extractor(str(e)):
            msg += (
                " — if the video plays in a browser, your yt-dlp is probably out "
                "of date. Update with: uv tool upgrade yotutil "
                "(or, in a checkout: uv sync --upgrade-package yt-dlp). "
                "If updating doesn't help, try setting player_clients in "
                "~/.config/yotutil/config.toml"
            )
        raise DownloadError(msg) from e

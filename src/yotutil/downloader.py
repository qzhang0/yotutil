"""yt-dlp wrapper for downloading and converting to MP3."""

import logging
import shutil
from functools import lru_cache

import yt_dlp
from yt_dlp.utils import remove_terminal_sequences

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
    # A 403 on the media URL means the player client we picked fell out of
    # favour with YouTube. It looks like a permissions error but the remedy is
    # the same as a stale extractor: update, or switch player_clients.
    "403: forbidden",
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


# Pad every line to a fixed width so a short line fully erases a longer one.
_PROGRESS_WIDTH = 60


def _plain(value: str) -> str:
    """Strip yt-dlp's terminal colour codes and surrounding whitespace.

    yt-dlp colours its progress strings when stdout is a terminal. The escapes
    inflate len() without taking up screen width, so a bare \\r overwrite would
    leave coloured fragments of the previous line behind. Use yt-dlp's own
    stripper so this tracks whatever escapes it decides to emit.
    """
    return remove_terminal_sequences(value).strip()


def _progress_hook(d: dict) -> None:
    """Display download progress on a single, self-erasing line."""
    if d["status"] == "downloading":
        pct = _plain(d.get("_percent_str", "?%"))
        speed = _plain(d.get("_speed_str", "?"))
        eta = _plain(d.get("_eta_str", "?"))
        line = f"  {pct} at {speed} ETA {eta}"
        print("\r" + line.ljust(_PROGRESS_WIDTH), end="", flush=True)
    elif d["status"] == "finished":
        # "processing", not "converting" — with --video nothing is transcoded.
        print("\r" + "  Download complete, processing...".ljust(_PROGRESS_WIDTH))


class _PostprocessorReporter:
    """Announce post-download stages, which otherwise run in total silence.

    Merging a large video takes minutes with no output at all, which is
    indistinguishable from a hang. yt-dlp also fires several stages twice, so
    repeats are suppressed.
    """

    LABELS = {
        "Merger": "Merging video and audio",
        "ExtractAudio": "Converting to MP3",
        "EmbedThumbnail": "Embedding cover art",
        "Metadata": "Writing metadata",
    }

    def __init__(self) -> None:
        self._last: str | None = None

    def __call__(self, d: dict) -> None:
        if d.get("status") != "started":
            return
        label = self.LABELS.get(d.get("postprocessor", ""))
        if label is None or label == self._last:
            return
        self._last = label
        print("\r" + f"  {label}...".ljust(_PROGRESS_WIDTH))


def _video_format(max_height: int | None, compatible: bool = False) -> str:
    """Format selector for video mode, optionally capped and/or codec-limited."""
    height = f"[height<={max_height}]" if max_height is not None else ""
    if compatible:
        # H.264 video + AAC audio is the combination essentially every player
        # opens, including QuickTime. Fall back to anything if YouTube doesn't
        # offer it for this video.
        return (
            f"bestvideo[vcodec^=avc1]{height}+bestaudio[acodec^=mp4a]/"
            f"bestvideo{height}+bestaudio/best"
        )
    if max_height is None:
        return "bestvideo*+bestaudio/best"
    # Caps size only — YouTube often serves AV1 at every height, so this does
    # not by itself guarantee a more widely-playable codec.
    return f"bestvideo{height}+bestaudio/best{height}"


def build_yt_dlp_opts(
    config: Config,
    output_dir: str | None = None,
    video: bool = False,
    max_height: int | None = None,
    compatible: bool = False,
) -> dict:
    """Build yt-dlp options dict from config.

    With `video`, keep the original video instead of extracting audio: pull the
    best video+audio streams and mux them, skipping the MP3 transcode entirely.
    `config.audio_quality` is an MP3 setting and has no effect in this mode.
    `max_height` and `compatible` only apply there too — audio streams have
    neither a frame height nor a video codec.
    """
    out = output_dir or config.output_dir

    opts: dict = {
        "format": (
            _video_format(max_height, compatible) if video else "bestaudio/best"
        ),
        "postprocessors": [],
        "outtmpl": f"{out}/%(title)s.%(ext)s",
        "progress_hooks": [_progress_hook],
        "postprocessor_hooks": [_PostprocessorReporter()],
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
    compatible: bool = False,
) -> None:
    """Download a single URL (video or playlist), as MP3 unless `video` is set."""
    check_ffmpeg()

    opts = build_yt_dlp_opts(
        config,
        output_dir,
        video=video,
        max_height=max_height,
        compatible=compatible,
    )
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

"""CLI entry point using Typer."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from yotutil.config import Config, load_config
from yotutil.downloader import DownloadError, download

app = typer.Typer(
    name="yotutil",
    help="Download YouTube music videos as MP3 files.",
    no_args_is_help=True,
)

# Shared by every command, so the flags and help text are declared once.
OutputDirOption = Annotated[
    str | None,
    typer.Option("--output-dir", "-o", help="Output directory for MP3 files"),
]
QualityOption = Annotated[
    str,
    typer.Option("--quality", "-q", help="Audio quality (0=best, 9=worst)"),
]
VerboseOption = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Enable debug logging"),
]
VideoOption = Annotated[
    bool,
    typer.Option(
        "--video",
        "-V",
        help="Keep the original video (.mp4) instead of converting to MP3",
    ),
]
MaxHeightOption = Annotated[
    int | None,
    typer.Option(
        "--max-height",
        help="Cap video height, e.g. 1080 or 720 (with --video; smaller files)",
    ),
]
CompatibleOption = Annotated[
    bool,
    typer.Option(
        "--compatible",
        "-c",
        help="Prefer H.264/AAC so the video plays in QuickTime (with --video)",
    ),
]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _validate_video_options(
    video: bool, max_height: int | None, compatible: bool
) -> None:
    """Reject video-only flags passed without --video.

    Both shape the video stream, so without --video they would do nothing at
    all. Saying so beats handing back an MP3 as though the flag had applied.
    """
    if video:
        return

    unused = [
        name
        for name, given in (
            ("--max-height", max_height is not None),
            ("--compatible", compatible),
        )
        if given
    ]
    if unused:
        raise typer.BadParameter(
            f"{' and '.join(unused)} only applies when downloading video. "
            "Add --video to keep the video, or drop the option to get an MP3."
        )


def _prepare(
    output_dir: str | None, quality: str, verbose: bool
) -> tuple[Config, str]:
    """Apply CLI overrides to the loaded config and ensure the output dir exists."""
    _setup_logging(verbose)
    config = load_config()
    config.audio_quality = quality

    out = output_dir or config.output_dir
    Path(out).mkdir(parents=True, exist_ok=True)
    return config, out


@app.command()
def dl(
    url: Annotated[str, typer.Argument(help="YouTube video or playlist URL")],
    output_dir: OutputDirOption = None,
    quality: QualityOption = "0",
    verbose: VerboseOption = False,
    video: VideoOption = False,
    max_height: MaxHeightOption = None,
    compatible: CompatibleOption = False,
) -> None:
    """Download a YouTube video or playlist as MP3, or as video with --video."""
    _validate_video_options(video, max_height, compatible)
    config, out = _prepare(output_dir, quality, verbose)

    typer.echo(f"Downloading: {url}")
    try:
        download(
            url,
            config,
            output_dir=out,
            video=video,
            max_height=max_height,
            compatible=compatible,
        )
        typer.echo("Done!")
    except DownloadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def batch(
    file: Annotated[
        Path, typer.Argument(help="Text file with one URL per line")
    ],
    output_dir: OutputDirOption = None,
    quality: QualityOption = "0",
    verbose: VerboseOption = False,
    video: VideoOption = False,
    max_height: MaxHeightOption = None,
    compatible: CompatibleOption = False,
) -> None:
    """Download multiple URLs from a text file as MP3s, or as videos with --video."""
    _validate_video_options(video, max_height, compatible)

    if not file.exists():
        typer.echo(f"Error: File not found: {file}", err=True)
        raise typer.Exit(code=1)

    urls = [
        line.strip()
        for line in file.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not urls:
        typer.echo("No URLs found in file.")
        raise typer.Exit(code=1)

    config, out = _prepare(output_dir, quality, verbose)

    typer.echo(f"Downloading {len(urls)} URL(s)...")
    failed = []
    for i, url in enumerate(urls, 1):
        typer.echo(f"\n[{i}/{len(urls)}] {url}")
        try:
            download(
                url,
                config,
                output_dir=out,
                video=video,
                max_height=max_height,
                compatible=compatible,
            )
        except DownloadError as e:
            typer.echo(f"  Failed: {e}", err=True)
            failed.append(url)

    if failed:
        typer.echo(f"\n{len(failed)} download(s) failed:")
        for url in failed:
            typer.echo(f"  - {url}")
        raise typer.Exit(code=1)

    typer.echo("\nAll downloads complete!")

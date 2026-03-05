"""CLI entry point using Typer."""

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from yotutil.config import load_config
from yotutil.downloader import DownloadError, download

app = typer.Typer(
    name="yotutil",
    help="Download YouTube music videos as MP3 files.",
    no_args_is_help=True,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


@app.command()
def dl(
    url: Annotated[str, typer.Argument(help="YouTube video or playlist URL")],
    output_dir: Annotated[
        Optional[str],
        typer.Option("--output-dir", "-o", help="Output directory for MP3 files"),
    ] = None,
    quality: Annotated[
        str,
        typer.Option("--quality", "-q", help="Audio quality (0=best, 9=worst)"),
    ] = "0",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging"),
    ] = False,
) -> None:
    """Download a YouTube video or playlist as MP3."""
    _setup_logging(verbose)
    config = load_config()
    config.audio_quality = quality

    out = output_dir or config.output_dir
    Path(out).mkdir(parents=True, exist_ok=True)

    typer.echo(f"Downloading: {url}")
    try:
        download(url, config, output_dir=out)
        typer.echo("Done!")
    except DownloadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def batch(
    file: Annotated[
        Path, typer.Argument(help="Text file with one URL per line")
    ],
    output_dir: Annotated[
        Optional[str],
        typer.Option("--output-dir", "-o", help="Output directory for MP3 files"),
    ] = None,
    quality: Annotated[
        str,
        typer.Option("--quality", "-q", help="Audio quality (0=best, 9=worst)"),
    ] = "0",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging"),
    ] = False,
) -> None:
    """Download multiple URLs from a text file as MP3s."""
    _setup_logging(verbose)

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

    config = load_config()
    config.audio_quality = quality

    out = output_dir or config.output_dir
    Path(out).mkdir(parents=True, exist_ok=True)

    typer.echo(f"Downloading {len(urls)} URL(s)...")
    failed = []
    for i, url in enumerate(urls, 1):
        typer.echo(f"\n[{i}/{len(urls)}] {url}")
        try:
            download(url, config, output_dir=out)
        except DownloadError as e:
            typer.echo(f"  Failed: {e}", err=True)
            failed.append(url)

    if failed:
        typer.echo(f"\n{len(failed)} download(s) failed:")
        for url in failed:
            typer.echo(f"  - {url}")
        raise typer.Exit(code=1)

    typer.echo("\nAll downloads complete!")

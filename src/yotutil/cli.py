"""CLI entry point using Typer."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from yotutil.config import Config, load_config
from yotutil import yoto
from yotutil.config import CONFIG_FILE
from yotutil.downloader import DownloadError, download

app = typer.Typer(
    name="yotutil",
    help="Download YouTube music videos as MP3 files.",
    no_args_is_help=True,
)
yoto_app = typer.Typer(
    help="Send MP3s to Yoto as a Make Your Own playlist.",
    no_args_is_help=True,
)
app.add_typer(yoto_app, name="yoto")

# Shared by every command, so the flags and help text are declared once.
OutputDirOption = Annotated[
    str | None,
    typer.Option("--output-dir", "-o", help="Output directory for MP3 files"),
]
# Defaults to None, not "0", so an explicit -q is distinguishable from an
# absent one. Without that, the CLI default silently overwrote audio_quality
# from config.toml on every run.
QualityOption = Annotated[
    str | None,
    typer.Option(
        "--quality",
        "-q",
        help="Audio quality (0=best, 9=worst)",
        show_default="0",
    ),
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


def _validate_output_options(
    video: bool, max_height: int | None, compatible: bool, quality: str | None
) -> None:
    """Reject flags that do nothing for the chosen output format.

    --max-height and --compatible shape the video stream, so they are inert
    without --video; --quality sets MP3 quality, so it is inert with it. Saying
    so beats silently producing a file the flag never influenced.
    """
    if video:
        if quality is not None:
            raise typer.BadParameter(
                "--quality sets MP3 quality and does nothing with --video. "
                "Drop it, or use --max-height to control the video size."
            )
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
    output_dir: str | None, quality: str | None, verbose: bool
) -> tuple[Config, str]:
    """Apply CLI overrides to the loaded config and ensure the output dir exists."""
    _setup_logging(verbose)
    config = load_config()
    # Only override when the flag was actually given, so config.toml survives.
    if quality is not None:
        config.audio_quality = quality

    out = output_dir or config.output_dir
    Path(out).mkdir(parents=True, exist_ok=True)
    return config, out


@app.command()
def dl(
    url: Annotated[str, typer.Argument(help="YouTube video or playlist URL")],
    output_dir: OutputDirOption = None,
    quality: QualityOption = None,
    verbose: VerboseOption = False,
    video: VideoOption = False,
    max_height: MaxHeightOption = None,
    compatible: CompatibleOption = False,
) -> None:
    """Download a YouTube video or playlist as MP3, or as video with --video."""
    _validate_output_options(video, max_height, compatible, quality)
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


def _parse_batch_file(text: str) -> list[tuple[str, str | None]]:
    """(url, optional track name) for each non-blank, non-comment line."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, *rest = line.split(maxsplit=1)
        entries.append((url, rest[0] if rest else None))
    return entries


@app.command()
def batch(
    file: Annotated[
        Path, typer.Argument(help="Text file with one URL per line")
    ],
    output_dir: OutputDirOption = None,
    quality: QualityOption = None,
    verbose: VerboseOption = False,
    video: VideoOption = False,
    max_height: MaxHeightOption = None,
    compatible: CompatibleOption = False,
    yoto_title: Annotated[
        str | None,
        typer.Option(
            "--yoto",
            help="Then upload the MP3s to Yoto as a new playlist with this title",
        ),
    ] = None,
) -> None:
    """Download multiple URLs from a text file as MP3s, or as videos with --video.

    Each line is a URL, optionally followed by a space and a track name for
    Yoto, e.g. "https://youtu.be/abc 小红帽". Lines starting with # are skipped.
    """
    _validate_output_options(video, max_height, compatible, quality)
    if yoto_title is not None and video:
        raise typer.BadParameter("--yoto uploads MP3s and can't be used with --video.")

    if not file.exists():
        typer.echo(f"Error: File not found: {file}", err=True)
        raise typer.Exit(code=1)

    entries = _parse_batch_file(file.read_text())
    if not entries:
        typer.echo("No URLs found in file.")
        raise typer.Exit(code=1)

    config, out = _prepare(output_dir, quality, verbose)

    typer.echo(f"Downloading {len(entries)} URL(s)...")
    failed = []
    to_upload: list[tuple[Path, str]] = []
    for i, (url, name) in enumerate(entries, 1):
        typer.echo(f"\n[{i}/{len(entries)}] {url}")
        try:
            paths = download(
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
            continue
        # A name only fits a single song; a playlist URL keeps its file names.
        for path in map(Path, paths):
            to_upload.append((path, name if name and len(paths) == 1 else path.stem))

    if failed:
        typer.echo(f"\n{len(failed)} download(s) failed:")
        for url in failed:
            typer.echo(f"  - {url}")
        if yoto_title is not None:
            typer.echo("Nothing was uploaded to Yoto. Fix or remove these and run again.")
        raise typer.Exit(code=1)

    typer.echo("\nAll downloads complete!")
    if yoto_title is not None:
        typer.echo("\nUploading to Yoto...")
        _send_to_yoto(yoto_title, to_upload)


def _yoto_client_id() -> str:
    client_id = load_config().yoto_client_id
    if not client_id:
        typer.echo(
            "Error: no Yoto client ID. Create an app at https://dashboard.yoto.dev, "
            f'then add this line to {CONFIG_FILE}:\n  yoto_client_id = "<your client ID>"',
            err=True,
        )
        raise typer.Exit(code=1)
    return client_id


@yoto_app.command()
def login(verbose: VerboseOption = False) -> None:
    """Sign in to Yoto in your browser. Needed once per computer."""
    _setup_logging(verbose)
    client_id = _yoto_client_id()
    try:
        yoto.login(client_id)
    except yoto.YotoError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo("Signed in to Yoto.")


@yoto_app.command()
def upload(
    files: Annotated[
        list[Path],
        typer.Argument(help="Audio files, in playlist order", exists=True, dir_okay=False),
    ],
    title: Annotated[str, typer.Option("--title", "-t", help="Playlist title")],
    verbose: VerboseOption = False,
) -> None:
    """Upload audio files to Yoto as a new Make Your Own playlist.

    Track names are the file names without the extension.
    """
    _setup_logging(verbose)
    _send_to_yoto(title, [(path, path.stem) for path in files])


def _send_to_yoto(title: str, items: list[tuple[Path, str]]) -> None:
    """Upload (file, track name) pairs in order, then create one playlist."""
    client_id = _yoto_client_id()
    try:
        tracks = []
        for i, (path, name) in enumerate(items, 1):
            typer.echo(f"[{i}/{len(items)}] Uploading {name}...")
            # Fetched per file: a long upload can outlast one access token.
            tracks.append(
                yoto.upload_track(path, yoto.access_token(client_id), title=name)
            )
        typer.echo(f"Creating playlist '{title}'...")
        new_id = yoto.create_playlist(title, tracks, yoto.access_token(client_id))
    except yoto.YotoError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo("No playlist was created.", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"Done! New Yoto playlist '{title}' has {len(tracks)} track(s) "
        f"(card ID {new_id}). Link it to a card in the Yoto app."
    )

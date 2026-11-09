"""CLI entry point using Typer."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from yotutil import yoto
from yotutil.config import CONFIG_FILE, Config, load_config
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


@contextmanager
def _exit_on(*errors: type[Exception], note: str | None = None) -> Iterator[None]:
    """Turn the given errors into a one-line message and exit code 1."""
    try:
        yield
    except errors as e:
        typer.echo(f"Error: {e}", err=True)
        if note:
            typer.echo(note, err=True)
        raise typer.Exit(code=1)


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
    with _exit_on(DownloadError):
        download(
            url,
            config,
            output_dir=out,
            video=video,
            max_height=max_height,
            compatible=compatible,
        )
    typer.echo("Done!")


@dataclass
class _BatchLine:
    url: str
    name: str | None = None  # Yoto track name
    icon: str | None = None  # Yoto icon: built-in icon word, image path, or yoto:#id


def _parse_batch_file(text: str) -> list[_BatchLine]:
    """Parse "URL [track name] [| icon]" lines, skipping blanks and # comments."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line, _, icon = line.partition("|")
        url, *rest = line.split(maxsplit=1)
        entries.append(_BatchLine(url, rest[0].strip() if rest else None, icon.strip() or None))
    return entries


# Shared by `batch --yoto` and `yoto upload`.
IconOption = Annotated[
    str | None,
    typer.Option(
        "--icon",
        help="Yoto icon for tracks without their own: a word to search Yoto's "
        "built-in icons (see `yotutil yoto icons`), or an image file",
    ),
]
CoverOption = Annotated[
    str | None,
    typer.Option("--cover", help="Playlist cover image: a file or an image URL"),
]


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
    icon: IconOption = None,
    cover: CoverOption = None,
) -> None:
    """Download multiple URLs from a text file as MP3s, or as videos with --video.

    Each line is a URL, optionally followed by a track name and an icon for
    Yoto: "https://youtu.be/abc 小白船 | boat". Lines starting with # are skipped.
    """
    _validate_output_options(video, max_height, compatible, quality)
    if yoto_title is not None and video:
        raise typer.BadParameter("--yoto uploads MP3s and can't be used with --video.")
    if yoto_title is None and (icon is not None or cover is not None):
        raise typer.BadParameter("--icon and --cover only apply with --yoto.")

    if not file.exists():
        typer.echo(f"Error: File not found: {file}", err=True)
        raise typer.Exit(code=1)

    entries = _parse_batch_file(file.read_text())
    if not entries:
        typer.echo("No URLs found in file.")
        raise typer.Exit(code=1)

    config, out = _prepare(output_dir, quality, verbose)
    client_id = None
    if yoto_title is not None:
        # Check Yoto set-up now, not after a long run of downloads.
        client_id = _yoto_client_id(config)
        with _exit_on(yoto.YotoError):
            yoto.access_token(client_id)

    typer.echo(f"Downloading {len(entries)} URL(s)...")
    failed = []
    to_upload: list[yoto.Item] = []
    for i, entry in enumerate(entries, 1):
        typer.echo(f"\n[{i}/{len(entries)}] {entry.url}")
        try:
            paths = download(
                entry.url,
                config,
                output_dir=out,
                video=video,
                max_height=max_height,
                compatible=compatible,
            )
        except DownloadError as e:
            typer.echo(f"  Failed: {e}", err=True)
            failed.append(entry.url)
            continue
        # A name only fits a single song; a playlist URL keeps its file names.
        # An icon suits every song from the line, so it applies to all of them.
        name = entry.name if len(paths) == 1 else None
        to_upload += [yoto.Item(path, name, entry.icon or icon) for path in paths]

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
        _send_to_yoto(client_id, yoto_title, to_upload, cover)


def _yoto_client_id(config: Config | None = None) -> str:
    client_id = (config or load_config()).yoto_client_id
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
    with _exit_on(yoto.YotoError):
        yoto.login(client_id)
    typer.echo("Signed in to Yoto.")


@yoto_app.command()
def upload(
    files: Annotated[
        list[Path],
        typer.Argument(help="Audio files, in playlist order", exists=True, dir_okay=False),
    ],
    title: Annotated[str, typer.Option("--title", "-t", help="Playlist title")],
    icon: IconOption = None,
    cover: CoverOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Upload audio files to Yoto as a new Make Your Own playlist.

    Track names are the file names without the extension.
    """
    _setup_logging(verbose)
    items = [yoto.Item(path, icon=icon) for path in files]
    _send_to_yoto(_yoto_client_id(), title, items, cover)


@yoto_app.command()
def icons(
    query: Annotated[
        str | None, typer.Argument(help="Word to search for, e.g. boat or moon")
    ] = None,
    verbose: VerboseOption = False,
) -> None:
    """Search Yoto's built-in icons. Use an icon's name with --icon or after | in a batch file."""
    _setup_logging(verbose)
    client_id = _yoto_client_id()
    with _exit_on(yoto.YotoError):
        found = yoto.public_icons(yoto.access_token(client_id))
    if query:
        found = yoto.search_icons(found, query)
    if not found:
        typer.echo(f"No icons match '{query}'.")
        raise typer.Exit(code=1)
    for item in found:
        tags = ", ".join(item.get("publicTags") or [])
        typer.echo(f"{item.get('title', '?'):<28} {tags}")
    typer.echo(f"\n{len(found)} icon(s). Preview them at https://yoto.dev/icons/using-icons")


def _send_to_yoto(
    client_id: str, title: str, items: list[yoto.Item], cover: str | None
) -> None:
    with _exit_on(yoto.YotoError, note="No playlist was created."):
        card_id = yoto.publish_playlist(
            title,
            items,
            lambda: yoto.access_token(client_id),
            cover=cover,
            progress=typer.echo,
        )
    typer.echo(
        f"Done! New Yoto playlist '{title}' has {len(items)} track(s) "
        f"(card ID {card_id}). Link it to a card in the Yoto app."
    )

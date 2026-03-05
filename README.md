# yotutil

Download YouTube Music videos as MP3 files — a thin, opinionated wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp).

![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)

## Why not just use yt-dlp directly?

You can. But if you want MP3 with metadata and thumbnails every time, the raw command is:

```
yt-dlp -x --audio-format mp3 --audio-quality 0 --embed-thumbnail --add-metadata \
  --extractor-args "youtube:player_client=web" <url>
```

yotutil wraps that into `yotutil dl <url>` with:

- **Zero-config defaults** — best quality, metadata + thumbnail embedded
- **Persistent config** — set your output directory once in `~/.config/yotutil/config.toml`
- **Batch downloads** — one URL per line, `#` comments supported, failures tracked
- **Auto JS-runtime detection** — tries deno / node / bun automatically

## Prerequisites

- **Python 3.13+** with [uv](https://github.com/astral-sh/uv)
- **FFmpeg** — required for audio conversion (`brew install ffmpeg` / `apt install ffmpeg`)
- **A JS runtime** — deno, node, or bun (at least one; needed by yt-dlp for YouTube extraction)

## Install

```bash
uv tool install git+https://github.com/qzhang/yotutil
```

Or clone and install locally:

```bash
git clone https://github.com/qzhang/yotutil
cd yotutil
uv tool install .
```

## Quick start

```bash
# Download a single video
yotutil dl "https://music.youtube.com/watch?v=..."

# Download to a specific folder
yotutil dl "https://music.youtube.com/watch?v=..." -o ~/Music

# Batch download from a file
yotutil batch urls.txt -o ~/Music
```

## Commands

### `yotutil dl <url>`

Download a single YouTube video or playlist as MP3.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output-dir` | `-o` | `.` (or config) | Output directory |
| `--quality` | `-q` | `0` | Audio quality: `0`=best … `9`=worst |
| `--verbose` | `-v` | off | Enable debug logging |

### `yotutil batch <file>`

Download multiple URLs from a text file.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output-dir` | `-o` | `.` (or config) | Output directory |
| `--quality` | `-q` | `0` | Audio quality: `0`=best … `9`=worst |
| `--verbose` | `-v` | off | Enable debug logging |

The batch file format: one URL per line, blank lines and `#` comments ignored.

```
# My playlist
https://music.youtube.com/watch?v=aaa
https://music.youtube.com/watch?v=bbb

# Another track
https://music.youtube.com/watch?v=ccc
```

Failed downloads are reported at the end; the exit code is non-zero if any fail.

## Config file

Path: `~/.config/yotutil/config.toml`

All keys are optional — omit any to use the default.

```toml
output_dir     = "~/Music"     # default: "." (current directory)
audio_quality  = "0"           # default: "0" (best); yt-dlp scale 0–9
embed_metadata = true          # default: true
embed_thumbnail = true         # default: true
```

CLI flags override config values.

## Development

```bash
uv sync                  # install dependencies
uv run pytest tests/ -v  # run tests
uv run yotutil --help    # smoke test CLI
```

## Architecture

- `src/yotutil/cli.py` — Typer CLI entry point (`dl` and `batch` commands)
- `src/yotutil/downloader.py` — yt-dlp Python API wrapper (download, convert, progress)
- `src/yotutil/config.py` — TOML config loading from `~/.config/yotutil/config.toml`

## License

MIT

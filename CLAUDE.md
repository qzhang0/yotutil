# yotutil

YouTube Music to MP3 downloader CLI. Python 3.13+, managed with `uv`.

## Commands

```bash
uv sync                  # Install dependencies
uv run pytest tests/ -v  # Run tests
uv run yotutil --help    # CLI help
```

## Architecture

- `src/yotutil/cli.py` — Typer CLI entry point (`dl` and `batch` commands)
- `src/yotutil/downloader.py` — yt-dlp Python API wrapper (download, convert, progress)
- `src/yotutil/config.py` — TOML config loading from `~/.config/yotutil/config.toml`

## Key dependencies

- **yt-dlp** as Python library (not subprocess) — requires FFmpeg + a JS runtime (node/deno/bun) + EJS solver for YouTube extraction
- **Typer** for CLI (type-hint driven, built on Click)

## Gotchas

- yt-dlp YouTube extraction requires `js_runtimes` and `remote_components` options — without these, downloads fail with "video not available"
- yt-dlp's default player clients currently fail: `android_vr` 403s on media URLs, `tv` returns SABR-only formats, `web`/`ios`/`mweb` need a GVS PO token. We pin `player_client` to `web_embedded` first via `extractor_args` (`DEFAULT_PLAYER_CLIENTS`), overridable by `player_clients` in config.toml; revisit when YouTube changes
- `noprogress` must stay on, or yt-dlp's progress bar interleaves with `_progress_hook`
- yt-dlp colours progress strings with ANSI escapes only when stdout is a tty — strip them and pad lines, or `\r` leaves coloured fragments behind (not reproducible through a pipe)
- Postprocessor hook names are `Merger`, `ExtractAudio`, `Metadata`, `EmbedThumbnail`, `MoveFiles`; several fire twice, so `_PostprocessorReporter` dedupes consecutive repeats
- Audio quality uses yt-dlp scale: `0` = best, `9` = worst

## Notes

- `.claude/` is in `.gitignore` — keep AI session files out of the repo
- `.scratch/` is also gitignored — safe for local task notes and experiments

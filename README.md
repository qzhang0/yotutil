# yotutil

Save YouTube and YouTube Music videos to your computer as MP3 files, with the song title, artist, and cover art already filled in.

![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

---

## Before you start

yotutil has no window or buttons — you run it by typing commands into a program called **Terminal**. If you've never done that, it's fine; every command below can be copied and pasted.

**Opening Terminal:**

- **macOS** — press `Cmd` + `Space`, type `Terminal`, press `Enter`.
- **Linux** — press `Ctrl` + `Alt` + `T`, or search your applications for `Terminal`.

To run a command: click into the Terminal window, paste the line, and press `Enter`. When a command finishes, you'll get a fresh prompt line and can type the next one.

> **Note:** Some commands below start with `sudo`, which means "run this as the computer's administrator." It will ask for your login password. Nothing appears on screen as you type the password — that's normal. Type it and press `Enter`.

---

## Step 1 — Install the three things yotutil needs

yotutil relies on three free programs. Install them once and you're done forever.

| Program | What it's for |
|---------|---------------|
| **FFmpeg** | Converts the downloaded audio into an MP3 file |
| **Node** | YouTube requires it to unscramble video links |
| **uv** | Installs and runs yotutil itself |

Follow the section for your operating system.

### macOS

**1a. Install Homebrew** (a tool that installs other tools). Skip this if you already have it.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

This takes a few minutes and will ask for your password. At the end it may print two extra commands under a line saying **"Next steps"** — if it does, copy and run those too, then close and reopen Terminal.

**1b. Install FFmpeg and Node:**

```bash
brew install ffmpeg node
```

**1c. Install uv:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Now close Terminal and open a new window.** This matters — `uv` won't be found until you do.

### Linux

**1a. Install FFmpeg and Node.**

On Ubuntu, Debian, Linux Mint, or Pop!\_OS:

```bash
sudo apt update && sudo apt install -y ffmpeg nodejs
```

On Fedora, or Red Hat / CentOS / Rocky:

```bash
sudo dnf install -y ffmpeg nodejs
```

On Arch or Manjaro:

```bash
sudo pacman -S ffmpeg nodejs
```

**1b. Install uv:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Now close Terminal and open a new window**, so that `uv` becomes available.

### Check that all three worked

Run these three commands one at a time:

```bash
ffmpeg -version
node --version
uv --version
```

Each should print a version number — something like `ffmpeg version 8.0`, `v24.4.0`, or `uv 0.12.5`. The exact numbers don't matter.

If any of them says **`command not found`**, that program didn't install. Close Terminal, open a fresh window, and try that one command again. If it still fails, redo the install step for that program.

---

## Step 2 — Install yotutil

```bash
uv tool install git+https://github.com/qzhang0/yotutil
```

Check that it worked:

```bash
yotutil --help
```

You should see a list of commands. If you get `command not found: yotutil`, close Terminal and open a new window, then try again.

---

## Step 3 — Download your first song

Copy the address of a YouTube or YouTube Music page from your browser's address bar, and put it in quotes after `yotutil dl`:

```bash
yotutil dl "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
```

> **Keep the quotes.** YouTube addresses contain characters that Terminal will otherwise misread.

You'll see a progress percentage, then `Done!`.

**Where did the file go?** Into whatever folder Terminal was "in" when you ran the command — by default, your home folder. To choose a folder instead, add `-o` and the folder name:

```bash
yotutil dl "https://music.youtube.com/watch?v=dQw4w9WgXcQ" -o ~/Music
```

`~` is shorthand for your home folder, so `~/Music` is your Music folder. yotutil creates the folder if it doesn't exist.

Playlist addresses work too — yotutil downloads every track in the playlist.

---

## Keeping the video instead of just the audio

By default yotutil throws the picture away and keeps only the sound, as an MP3. Add `--video` to keep the whole thing as a video file (`.mp4`) instead:

```bash
yotutil dl "https://www.youtube.com/watch?v=..." --video -o ~/Movies
```

This works with `batch` too:

```bash
yotutil batch songs.txt --video -o ~/Movies
```

Nothing is re-encoded in this mode, so it's quicker and the quality is exactly what YouTube served. The title, creator, and cover image are still saved into the file.

### Keeping the file size sane

By default `--video` takes the highest quality YouTube offers, which for a long
concert in 4K can mean **well over 10 GB**. Use `--max-height` to cap it:

```bash
yotutil dl "https://www.youtube.com/watch?v=..." --video --max-height 1080
```

Measured on one 83-minute 4K concert video:

| Setting | Result |
|---------|--------|
| no cap | 4K, **12.9 GB** |
| `--max-height 1080` | 1080p, **2.2 GB** |
| `--max-height 720` | 720p, **1.1 GB** |

`1080` is a good default for watching on a laptop or TV; `720` if you mainly care
about having a copy.

### If the video won't play

YouTube usually serves a modern format called AV1, which Apple's QuickTime
Player cannot open at any resolution. Add `--compatible` to get the older,
universally supported H.264 format instead:

```bash
yotutil dl "https://www.youtube.com/watch?v=..." --video --compatible
```

The file will be somewhat larger for the same quality — on the concert above,
1080p grows from 2.2 GB to 3.2 GB — because the older format compresses less
efficiently. If YouTube doesn't offer H.264 for a particular video, yotutil
falls back to the best available rather than failing.

The alternative is to keep the smaller file and use [VLC](https://www.videolan.org/),
which is free and plays essentially anything.

Two more things to know:

- The `--quality` option does nothing with `--video`. It sets MP3 quality, and no MP3 is being made.
- Downloading a large video to a network drive or NAS can stall during the final merge step. Download to a local folder first, then copy the finished file across.

---

## Downloading many songs at once

Make a plain text file with one address per line. Lines starting with `#` are notes and are ignored, as are blank lines.

Save it as `songs.txt`:

```
# Morning playlist
https://music.youtube.com/watch?v=aaa
https://music.youtube.com/watch?v=bbb

# Something else
https://music.youtube.com/watch?v=ccc
```

Then run:

```bash
yotutil batch songs.txt -o ~/Music
```

If some downloads fail, the rest still finish, and the failed addresses are listed at the end so you can retry them.

---

## Always saving to the same folder (optional)

If you're tired of typing `-o ~/Music` every time, you can set it once.

Create the settings folder and open a new settings file:

```bash
mkdir -p ~/.config/yotutil
nano ~/.config/yotutil/config.toml
```

`nano` is a simple text editor that runs inside Terminal. Paste this in:

```toml
output_dir = "~/Music"
```

Then press `Ctrl` + `O` and `Enter` to save, and `Ctrl` + `X` to quit.

From now on, plain `yotutil dl "..."` saves to your Music folder.

All available settings — every one is optional:

```toml
output_dir      = "~/Music"   # where MP3s are saved (default: current folder)
audio_quality   = "0"         # 0 = best quality, 9 = smallest file
embed_metadata  = true        # write title and artist into the file
embed_thumbnail = true        # use the video thumbnail as cover art
```

Options typed on the command line always win over the settings file.

There is one more setting, `player_clients`, which most people will never need — see [When downloads suddenly stop working](#when-downloads-suddenly-stop-working) below.

---

## Command reference

### `yotutil dl <address>`

Download one video or playlist as MP3.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output-dir` | `-o` | current folder (or settings file) | Where to save the MP3s |
| `--quality` | `-q` | `0` | Audio quality: `0` = best … `9` = worst (MP3 only; ignored with `--video`) |
| `--verbose` | `-v` | off | Print detailed technical output |
| `--video` | `-V` | off | Keep the original video (`.mp4`) instead of converting to MP3 |
| `--max-height` | | none | Cap video height, e.g. `1080` or `720` (with `--video`) |
| `--compatible` | `-c` | off | Prefer H.264/AAC so the file plays in QuickTime (with `--video`) |

### `yotutil batch <file>`

Download every address listed in a text file.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output-dir` | `-o` | current folder (or settings file) | Where to save the MP3s |
| `--quality` | `-q` | `0` | Audio quality: `0` = best … `9` = worst (MP3 only; ignored with `--video`) |
| `--verbose` | `-v` | off | Print detailed technical output |
| `--video` | `-V` | off | Keep the original video (`.mp4`) instead of converting to MP3 |
| `--max-height` | | none | Cap video height, e.g. `1080` or `720` (with `--video`) |
| `--compatible` | `-c` | off | Prefer H.264/AAC so the file plays in QuickTime (with `--video`) |

---

## Troubleshooting

| What you see | What to do |
|--------------|------------|
| `command not found: yotutil` | Close Terminal, open a new window, try again. If it persists, rerun Step 2. |
| `command not found: uv` | Same fix — a new Terminal window. `uv` is only found by windows opened after it was installed. |
| `ffmpeg is required but not found` | FFmpeg didn't install. Rerun step 1b for your system. |
| `A JavaScript runtime (deno, node, or bun) is required` | Node didn't install. macOS: `brew install node`. Linux: rerun step 1a. |
| `This video is not available` — but it plays fine in your browser | YouTube changed something and your copy of yotutil is out of date. Run `uv tool upgrade yotutil`. |
| `HTTP Error 403: Forbidden` | Usually also fixed by `uv tool upgrade yotutil`. YouTube periodically blocks the method used to fetch audio, and updates restore it. |
| Downloads are very slow | Normal — YouTube limits download speed. A typical song still takes only a few seconds. |

To update yotutil at any time:

```bash
uv tool upgrade yotutil
```

It's worth doing whenever downloads start failing. Most breakage is YouTube changing things, and updating is the fix.

### When downloads suddenly stop working

YouTube regularly changes how it hands out audio, which breaks downloading for everyone at once. Updating (above) fixes this most of the time.

If updating doesn't help, there's a fallback. yotutil can ask YouTube for audio while pretending to be any of several different kinds of player, and when one stops working another often still does. To change which ones it tries, add a `player_clients` line to your settings file (`~/.config/yotutil/config.toml`):

```toml
player_clients = ["web_embedded", "tv", "android_vr", "default"]
```

That's the built-in order. Try moving a different name to the front — `tv`, `android_vr`, `mweb`, `web`, and `ios` are all valid. If none work, it's likely a YouTube change that needs a yt-dlp update, so check back after upgrading.

---

## A note on what you download

Please only download content you have the right to — material you own, that's licensed for reuse, or that the creator permits saving. Copyright law and YouTube's Terms of Service still apply to whatever you do with this tool.

---

## For developers

<details>
<summary>Development setup, architecture, and design notes</summary>

### Setup

```bash
git clone https://github.com/qzhang0/yotutil
cd yotutil
uv sync                  # install dependencies
uv run pytest tests/ -v  # run tests
uv run yotutil --help    # smoke test the CLI
```

To install your local checkout as a global command: `uv tool install .`

### Architecture

- `src/yotutil/cli.py` — Typer CLI entry point (`dl` and `batch` commands)
- `src/yotutil/downloader.py` — yt-dlp Python API wrapper (download, convert, progress)
- `src/yotutil/config.py` — TOML config loading from `~/.config/yotutil/config.toml`

### Why not just call yt-dlp directly?

You can. yotutil is a thin, opinionated wrapper over [yt-dlp](https://github.com/yt-dlp/yt-dlp) that fixes a working set of options so MP3 + metadata + thumbnail is the zero-argument default, adds a persistent config file, and handles batch input with per-URL failure tracking.

### YouTube extraction gotchas

yt-dlp needs more than default options to pull YouTube audio:

- **`js_runtimes` + `remote_components`** — YouTube requires solving a JS challenge; without these, extraction fails with "video is not available". Runtimes are auto-detected from deno / node / bun.
- **`extractor_args.youtube.player_client`** — yt-dlp's default client picks currently fail: `android_vr` returns media URLs that 403, `tv` yields SABR-only formats with no URL, and `web` / `ios` / `mweb` demand a GVS PO token. `web_embedded` still serves plain HTTPS audio formats, so it's tried first with the others as fallbacks (`DEFAULT_PLAYER_CLIENTS` in `downloader.py`). This list is expected to rot, so `player_clients` in `config.toml` overrides it — users can route around the next breakage without waiting for a release.
- **`noprogress`** — suppresses yt-dlp's own progress bar so it doesn't interleave with ours.

Audio quality uses the yt-dlp scale, where `0` is best and `9` is worst.

</details>

## License

[MIT](LICENSE)

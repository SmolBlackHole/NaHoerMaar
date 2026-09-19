# Develop NaHörMaar

Parent: [Project README](../README.md)

Python 3.12+ and Node.js 22+ with npm are required.

## Set up

From the repository root:

```powershell
python scripts/dev.py setup
```

Linux and macOS can also use `./scripts/setup.sh`. If PowerShell blocks
`scripts/setup.ps1`, use the Python command above.

Setup installs a local FFmpeg binary through `imageio-ffmpeg` and checks that it
runs. You can override it with `FFMPEG_PATH`. On Linux and macOS, audio processing
also needs the system Opus library (`libopus0` on Debian/Ubuntu,
`opus` through Homebrew on macOS).

## Try Discord playback

Copy `.env.example` to `.env` and set `DISCORD_TOKEN` and `DISCORD_GUILD_ID`.
Environment variables override `.env` values. Invite the bot to that server with
View Channel, Connect and Speak permissions for the test voice channel.

List the available voice channels:

```powershell
.venv\Scripts\python.exe scripts/live_playback.py
```

Use a channel ID from that output and two public YouTube video URLs:

```powershell
.venv\Scripts\python.exe scripts/live_playback.py --channel CHANNEL_ID "YOUTUBE_URL_1" "YOUTUBE_URL_2"
```

The script plays both tracks in order and exits after the queue finishes. Add
`--controls` to exercise pause, resume, volume, skip and stop with short delays.
Use tracks long enough to hear each operation. Ctrl+C stops playback and closes
the connection. On Linux/macOS, use `.venv/bin/python` for these commands.

At 100% volume, compatible Opus streams play without re-encoding. Lowering the
global volume changes the audio for everyone and requires re-encoding. Returning
to 100% restores passthrough without restarting the track.

The script uses `data/live-test.sqlite3` and replaces that test queue on each
playback run. The normal runtime uses `DATABASE_PATH`, defaulting to
`data/player.sqlite3`.

Local tests cover real FFmpeg decoding, failure recovery and callback races.
Live acceptance still requires two consecutive tracks and the playback controls
to work in Discord. Disconnect the bot during playback and check that audio stops
without consuming the interrupted entry. Repeat on the deployment host before
shared use.

## Daily bio

The bot sets its application description to a daily quote from
[quotes.toml](../quotes.toml). Add entries to the `de`, `en` and `nl` arrays under
`[quotes]`, with up to 400 characters per quote.

The selection stays the same throughout the bot host's local calendar day,
including after a restart, as long as the list is unchanged. While running, the
bot checks for a new day once a minute. Changes to the list take effect the next
day or after a restart. Failed updates leave playback running and retry after a
minute.

## Run checks

```powershell
python scripts/dev.py check
```

This runs:

- Repository text and documentation link checks.
- Ruff linting and formatting, strict mypy and Pyright, and pytest for Python.
- Node.js syntax checks and tests for the frontend.

Use `npm run check` to check only the frontend. Shell wrappers are available as
`scripts/check.sh` and `scripts/check.ps1`.

GitHub Actions runs the same checks on Windows and Linux.

## Editor

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

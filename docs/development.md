# Develop NaHörMaar

Parent: [Project README](../README.md)

Python 3.12+ and Node.js 24.11+ with npm are required.

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

## Run the dashboard

```powershell
npm run dev
```

Open `http://127.0.0.1:3000` and run the API in a second terminal. Choose a name,
join a voice channel, add a YouTube video link and press play. On PowerShell
systems that block `npm.ps1`, use `npm.cmd run dev`.

The dashboard forwards controls and live updates to `http://127.0.0.1:8000`.
Set `NUXT_BACKEND_URL` if the backend uses a different port. Controls stay disabled
until live state arrives. Search and playlist import are not available yet.

Click or drag the player timeline to seek for everyone in the Discord channel.
Arrow keys adjust the focused slider. Paused tracks stay paused after seeking.

Cover and Video fill the player area behind the track details. Video starts muted;
the settings button reveals
YouTube's controls and quality choices. They only affect your browser. Sync returns
the preview to the bot's position. Videos that cannot be embedded fall back to their
cover and source link.
The Queue tab includes Recently played, where you can requeue the last 100 started
tracks. Repeated songs appear once with their play count. It shows five songs
until you choose Show all.

## Configure Discord

Copy `.env.example` to `.env` and set `DISCORD_TOKEN` and `DISCORD_GUILD_ID`.
Environment variables override `.env` values. Invite the bot to that server with
View Channel, Connect and Speak permissions for the test voice channel.

## Run the API

```powershell
.venv\Scripts\python.exe -m nahormaar_backend
```

Open [the API explorer](http://127.0.0.1:8000/docs) to try the controls. Use
`GET /api/channels` to find a channel, connect with `PUT /api/voice/channel`, add
a video through `POST /api/queue`, then call `POST /api/player/play` with
`expected_playback_id: null`.

Every mutation needs a UUID in its `Idempotency-Key` header. Generate one with
`[guid]::NewGuid().ToString()` in PowerShell. Reuse it only when retrying the same
request. See the [API contract](api.md) for payloads and conflict handling.

The server binds to `127.0.0.1:8000` and runs one bot instance. Keep one worker;
multiple workers would each start a bot and own a different player. Browser
profiles are local names and avatars, not access control. Discord login and the
whitelist are still pending, so shared deployment is not supported yet. Both the
API and the dashboard proxy accept only local hosts and reject requests from
other origins.

Ctrl+C closes the HTTP event streams, stops audio and disconnects the bot. The
queue survives and waits for a manual start after restarting the backend.

## Try Discord playback without the API

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

Two consecutive tracks and the playback controls have been tested in Discord.
Live checks for an unavailable track, an unexpected voice disconnect and the
visible presence remain open. During the disconnect check, audio must stop and
the interrupted entry must return to the queue. Repeat playback acceptance on
the deployment host before shared use.

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
- Frontend tests, Nuxt type checking and a production build.

Use `npm run check` to check only the frontend. Shell wrappers are available as
`scripts/check.sh` and `scripts/check.ps1`.

GitHub Actions runs the same checks on Windows and Linux.

## Editor

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

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

Open `http://localhost:3012` and run the API in a second terminal. Sign in with
Discord, choose a name, join a voice channel, add a track and press play. On PowerShell
systems that block `npm.ps1`, use `npm.cmd run dev`.

The dashboard forwards controls and live updates to `http://127.0.0.1:8000`.
Set `NUXT_BACKEND_URL` if the backend uses a different port. Controls stay disabled
until live state arrives.

In Queue, enter a title or artist to search YouTube Music songs. Choose Videos
for a YouTube video search, or paste a video link to add it directly. Load more
returns the next ten results, up to 100. Searches are cached for five minutes.
Playlist links open a preview with up to 100 entries. Select tracks
or keep all available entries selected, then add them in playlist order. Cancel
stops preparation without changing the queue. For links containing both a video
and a playlist, Add track adds the video; Open playlist instead opens the preview.

Drag a queue entry by its position number, or choose Move to position in its menu.
The same menu offers Move up and Move down for keyboard use. Wait estimates switch
to hours and minutes at one hour, for example `In ~01:15 h`.

The Remove menu can clear your tracks, another person's tracks, or the entire
queue. It shows whose tracks will be removed and how many before confirmation,
and leaves playback running.
The removal toast offers Undo for 12 seconds. It restores the removed entries
without reverting anyone else's later changes.
Ownership follows your account, even if you change its name or use another device.

Click or drag the player timeline to seek for everyone in the Discord channel.
Arrow keys adjust the focused slider. Paused tracks stay paused after seeking.

Open Audio settings from the speaker button for volume and crossfade. Crossfade
starts disabled; enabling it selects five seconds, adjustable from three to seven.
It applies to everyone in the channel and survives backend restarts. Changes
affect the next natural transition. Short tracks shorten the overlap; unknown
duration or unavailable preparation uses the ordinary track change. The title
and progress switch to the incoming song when its fade begins.

Cover and Video fill the player area behind the track details. Video starts muted;
the settings button reveals
YouTube's controls and quality choices. They only affect your browser. Sync returns
the preview to the bot's position. Videos that cannot be embedded fall back to their
cover and source link.
The Queue tab includes Recently played, where you can requeue the last 100 started
tracks. Repeated songs appear once with their play count. It shows five songs
until you choose Show all.

## Configure Discord

Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
Environment variables override `.env` values. Invite the bot with View Channel,
Connect and Speak permissions for the voice channels you want to use.

Open the Discord selector in the sidebar to choose a server and voice channel.
It discovers the bot's servers automatically, including new invitations, without
a restart or a guild ID in the configuration. Switching channels retains the
current position and resumes playback after joining. An explicitly paused track
stays paused. Joining with no current track starts queued work.

For dashboard sign-in, set these additional values in `.env`:

```dotenv
DISCORD_CLIENT_ID=YOUR_APPLICATION_ID
DISCORD_CLIENT_SECRET=YOUR_CLIENT_SECRET
PUBLIC_ORIGIN=http://localhost:3012
```

In the application's OAuth2 settings, register
`http://localhost:3012/api/auth/discord/callback` as a redirect URI. Use the same
host and port when opening the dashboard. The bot token and OAuth client secret
are different credentials.

Copy `access.example.toml` to `access.toml` and add the Discord user IDs allowed
to use the bot as quoted strings in `discord_ids`. Enable Discord's Developer
Mode to copy a user's ID. All listed users can control playback and edit the
whole queue. Changes take effect without a restart; removing someone closes
their live updates within five seconds. A missing or invalid access file blocks
access. Both `.env` and `access.toml` stay out of Git.

Use `/pspsps` in Discord to bring the bot to your current voice channel. The
command checks this same whitelist on every call; a dashboard account is not
required. Its replies are visible only to you. Join a regular voice channel first;
the bot needs View Channel, Connect and Speak there. Calling it in the bot's
current channel leaves active playback alone. If playback was interrupted, it
resumes at the retained position; if there is no current track, queued work starts.
An explicit pause remains paused. Moving channels follows the same behavior as
the dashboard. A successful command replies `:3`.

The API runtime registers `/pspsps` globally during Discord login, so it is
available on the bot's servers without configuring guild IDs. The bot invitation
must include the `applications.commands` scope. Command registration failures
are logged without preventing music playback. The standalone playback test script
does not register commands.

Sessions last seven days and survive backend restarts. Names and avatars belong
to the account and work across devices. An old browser profile can suggest a
name and avatar at first sign-in, but its past queue entries are not reassigned.
Signing out affects other tabs using that session and leaves music playing.

## Run the API

```powershell
.venv\Scripts\python.exe -m nahormaar_backend
```

The [API explorer](http://127.0.0.1:8000/docs) describes the endpoints. Use the
dashboard for playback checks: API calls require its session cookie, and mutations
also require its CSRF token and configured origin.

Every playback or queue mutation needs a UUID in its `Idempotency-Key` header. Generate one with
`[guid]::NewGuid().ToString()` in PowerShell. Reuse it only when retrying the same
request. See the [API contract](api.md) for payloads and conflict handling.

The server binds to `127.0.0.1:8000` and runs one bot instance. Keep one worker;
multiple workers would each start a bot and own a different player. Keep FastAPI
internal and route dashboard requests through Nuxt. `PUBLIC_ORIGIN` fixes the
allowed browser origin and OAuth redirect; forwarded host headers do not override
it. The dev command reads the root `.env`. A deployed Nuxt server needs the same
value in its environment, with HTTPS for secure cookies. Deployment remains a
separate step.

Ctrl+C closes the HTTP event streams, saves the audio position, stops audio and
disconnects the bot. On the next startup it rejoins the last channel and resolves
a fresh stream for the same track at the saved position. Volume is restored;
paused tracks stay paused. The queue and play counts remain unchanged. An idle
connected bot rejoins without starting the queue. An explicit Leave clears this
intent, so the next startup stays disconnected.

Position checkpoints are also saved every five seconds. After a crash playback
can rewind by roughly that interval. A restart during crossfade resumes the
incoming track without replaying the outgoing tail. Radio replenishment remains
off after restarting; entries it already queued remain available. If the saved
channel is gone or inaccessible, the dashboard reports the issue and retains the
interrupted track and position for a manual join.

The first upgrade from a version without checkpoints cannot recover its previous
channel or position. It retains the queue for a manual join and start; subsequent
restarts use the new checkpoint behavior. Ask before restarting a bot in active use.

### Playback diagnostics

The backend console includes timestamps and the owning process ID. Application
events identify playback attempts, queue entries, commands and their account IDs.
`playback.completed` records the audio position and expected duration;
`playback.failed` distinguishes retry from skip. `crossfade.*` events report
preparation, activation, fallback and completion. `audio.underrun` and
`audio.recovered` mark buffer stalls, not every audio frame.

Each FFmpeg child logs its process ID, exit code and whether cleanup killed it.
Its stderr is drained into a bounded list of diagnostic categories, such as
`http_403`, `timeout` or `invalid_media`. Raw output, media URLs, headers, tokens
and exception messages are excluded. Unknown output is labeled `ffmpeg_message`.
Capture the backend console when investigating a cut; the playback history alone
records starts and cannot explain why a track ended.

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

Earlier versions were exercised in Discord with consecutive tracks, controls,
an unavailable track and a voice disconnect. Those checks do not establish live
acceptance of the current Session/FSM refactor. Its offline checks use the test
transport described below. A new shared listening/reconnect check is still
required, including confirmation of what a Discord listener actually hears.

## Daily bio

The bot sets its application description to a daily quote from
[quotes.toml](../quotes.toml). Add entries to the `de`, `en` and `nl` arrays under
`[quotes]`, with up to 400 characters per quote.

The selection stays the same throughout the bot host's local calendar day,
including after a restart, as long as the list is unchanged. While running, the
bot checks for a new day once a minute. Changes to the list take effect the next
day or after a restart. Failed updates leave playback running and retry after a
minute.

## Database changes

Update the [SQLAlchemy mappings](../backend/src/nahormaar_backend/persistence/models.py),
then generate and review an Alembic migration:

```powershell
python -m alembic revision --autogenerate -m "Describe the change"
```

Migrations live in `backend/src/nahormaar_backend/persistence/migrations/versions/`.
The backend applies pending migrations on startup. `alembic.ini` points to
`data/player.sqlite3`; adjust `sqlalchemy.url` when developing against another
database. Start the backend once before generating a migration for a database
created before Alembic.

Appearance preferences are saved with the signed-in account. The **Cookies**
action in the sidebar opens the browser's YouTube consent settings. Covers and
video previews load only after consent; Discord audio works without it.

## License inventory

The Licenses page lists installed JavaScript and Python packages, fonts, artwork
and the selected FFmpeg build. Development startup and production builds refresh
the inventory; run `npm run licenses` to refresh it separately. Packages without
a bundled license text are marked and link to their source. Run setup first so
both the Python and Node.js dependencies are available.

## Run checks

```powershell
python scripts/dev.py check
```

This runs:

- Repository text and documentation link checks.
- Ruff linting and formatting, strict mypy and Pyright, and pytest for Python.
- Frontend tests, Nuxt type checking and a production build.

Backend tests use a separate working directory and database per test, synthetic
Discord credentials and simulated voice connections. Real Discord login, external
socket connections and connections to the local dev-server ports are blocked.
HTTP integration tests use an in-process app or a temporary loopback server.
Audio tests encode generated fixtures locally and never play them in Discord.
This isolates application state, not the host's CPU or process environment. Two
live interruptions coincided with audio checks and remain unexplained. Run these
checks while the bot is on a test server until the new diagnostics establish the
cause; do not treat the fixtures as a guarantee of no effect on shared playback.

For an offline check of the complete audio pipeline:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_audio_pipeline.py --basetemp=tmp/audio-check -p no:cacheprovider
```

This uses the real playback controller, SQLite persistence, FFmpeg, Opus and
discord.py audio thread. Only media lookup and Discord transport are replaced:
generated tones play into a local recording, with no bot login or calls to the
running API. It checks three- and five-second overlaps, pause and volume during
a fade, cleanup, and restoring a playing or paused track after a test-instance
restart. `crossfade.wav` and `measurement.json` in each crossfade test directory
contain the recording and signal measurements. Reusing this `--basetemp` replaces
the previous test output.

The uninterrupted-transition checks require both tones to overlap without a
silent frame or an abrupt gain change in the decoded Opus output. The pause check
measures the mixer PCM, including the first resumed frame, to separate it from
the Opus decoder's gain ramp after Discord's pause-silence packets. The output
recording still contains those packets. These checks do not
prove network reliability, YouTube availability or what actual Discord clients
hear. A shared listening check still needs a separately approved live session.

### Refactor acceptance

Run the complete backend suite in two sequential groups to keep the real audio
work separate from application/API checks:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests --ignore=backend/tests/test_audio_sources.py --ignore=backend/tests/test_audio_mixer.py --ignore=backend/tests/test_audio_quality.py --ignore=backend/tests/test_audio_pipeline.py --ignore=backend/tests/test_discord_voice.py --basetemp=tmp/acceptance-core
.venv\Scripts\python.exe -m pytest backend/tests/test_audio_sources.py backend/tests/test_audio_mixer.py backend/tests/test_audio_quality.py backend/tests/test_discord_voice.py backend/tests/test_audio_pipeline.py --basetemp=tmp/acceptance-audio
```

The first group includes a short FFmpeg diagnostic check against a missing local
file and isolated process tests. The second includes real decoding, mixing, Opus
and recorded audio-thread output. Together they cover every backend test. Run
only one group at a time and choose unused temporary directories when retaining
previous measurements. Run frontend checks and builds after the audio group.

Before a live acceptance run, agree on a test channel, tracks and permission to
restart or control the bot. Check natural completion and a crossfade by listening,
then pause/resume and seek. Disconnect/rejoin and `/pspsps` must resume the retained
track; joining the already active channel must not restart it. Finally restart a
playing and a paused track and check channel, position, pause, queue and history.
Keep the timestamped logs for each observation. Clean up only agreed test entries.
Passing offline checks cannot replace this listening/reconnect acceptance.

Use `npm run check` to check only the frontend. Shell wrappers are available as
`scripts/check.sh` and `scripts/check.ps1`.

GitHub Actions runs the same checks on Windows and Linux.

## Editor

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

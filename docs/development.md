# Develop NaHörMaar

Parent: [Project README](../README.md)

The normal backend start and local service now run the new engine. Read the
[engine/API development notes](engine-api.md) before restarting. The first cutover
uses `data/engine.sqlite3`; subsequent starts reopen it. Existing data was not imported.
The frontend uses the native engine API for discovery and session controls.

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

Open `http://localhost:3012`. On PowerShell systems that block `npm.ps1`,
use `npm.cmd run dev`. Sign in to load the current session, queue and history.

The Nuxt server forwards requests to `http://127.0.0.1:8000`; set
`NUXT_BACKEND_URL` when using another port. Its route allowlist and discovery
parameters follow the documented [native API](engine-api.md).

## Configure Discord

Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
Environment variables override `.env` values. Invite the bot with View Channel,
Connect and Speak permissions for the voice channels you want to use.

The bot discovers its joined servers and voice channels automatically; no guild
ID is configured. `GET /api/channels` lists them with permissions. Select a channel
through `PUT /api/connection`, or use `/pspsps` in Discord after configuration.

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
are logged without preventing music playback.

Sessions last seven days and survive backend restarts. Names and avatars belong
to the account and work across devices. An old browser profile can suggest a
name and avatar at first sign-in, but its past queue entries are not reassigned.
Signing out affects other tabs using that session and leaves music playing.

## Run the API

```powershell
.venv\Scripts\python.exe -m nahormaar_backend
```

The [API explorer](http://127.0.0.1:8000/docs) describes the new endpoints.
API calls require the authentication session cookie, and mutations also require
its CSRF token and configured origin. The dashboard retains the original
idempotency key and command body when checking a lost mutation response.

Every playback or queue mutation needs a UUID in its `Idempotency-Key` header. Generate one with
`[guid]::NewGuid().ToString()` in PowerShell. Reuse it only when retrying the same
request. See the [engine API contract](engine-api.md) for payloads and conflict handling.

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

The agreed first start of the engine uses an empty database. Previous queue,
history, accounts and preferences are not imported. Subsequent restarts use the
new engine's checkpoint behavior. Ask before restarting a bot in active use.

### Playback diagnostics

The backend console includes timestamps and the owning process ID.
`engine.runtime.ready` identifies the listening session and active schema.
`engine.audio.completed` records attempt ID, measured position and completion
reason. `engine.playback.*` reports failed effects or checkpoints;
`engine.radio.*` reports failed refill work. Audio-buffer and FFmpeg lifecycle
messages come from the reused technical adapters.

Raw media URLs, headers and tokens are excluded from audio diagnostics. Keep the
timestamped console output when investigating a cut: history records confirmed
starts and endings, but it cannot describe everything a Discord listener hears.

Do not start a second bot instance for acceptance. Use the native API and the
agreed channel on the single running service. The legacy standalone playback
script has been removed.

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

The playback/catalog mappings live in
[engine/persistence.py](../backend/src/nahormaar_backend/engine/persistence.py);
account mappings live in [persistence/models.py](../backend/src/nahormaar_backend/persistence/models.py).
Their combined metadata is owned by `engine/schema.py`. The frozen Alembic
chain lives under `engine/migrations/versions/`.

`alembic.ini` targets `data/engine.sqlite3`, not the retired player database.
Use an isolated database by changing `sqlalchemy.url` before developing migrations:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe the change"
```

Review generated migrations. Startup currently accepts only the pinned engine
revision and initializes a fresh database atomically; it does not automatically
upgrade an older or unrelated schema. When introducing a new engine revision,
update that explicit startup policy and its tests as part of the schema change.
Never rewrite an already applied migration or point development tooling at a live
database. No migration from the retired player data is provided.

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
This isolates application state, not host CPU usage. Run resource-heavy audio
checks only in an agreed window, without active shared listening.

### Backend checks and recorded audio

Run the backend suite on its own:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests
```

The standard suite includes generated local audio fixtures for the retained audio
adapters. Six full engine recordings are opt-in. In an agreed resource window:

```powershell
$env:NAHORMAAR_ENGINE_AUDIO_TESTS = "1"
.venv\Scripts\python.exe -m pytest backend/tests/engine/test_playback_pipeline.py --basetemp=tmp/engine-audio-check -p no:cacheprovider
Remove-Item Env:NAHORMAAR_ENGINE_AUDIO_TESTS
```

These use the real Session, SQLAlchemy, FFmpeg/Opus and the Discord audio thread.
Only lookup and network transport are replaced; generated tones reach a local
recording. They check overlap, pause/seek, source failure and restart behavior.
They neither log the bot in nor call its live API. Reusing `--basetemp` replaces
previous output, so choose a fresh directory to retain earlier measurements.

Before live acceptance, agree on a channel, tracks and permission to control or
restart the bot. Listen to natural completion and crossfade, then check pause/seek,
disconnect/rejoin, `/pspsps` and restart with playing and paused intent. Keep logs
and verify queue/history alongside what was actually audible. Clean up only
agreed test entries. Passing recordings and transport checks cannot replace
listener confirmation. The cutover's listening check remains open.

Use `npm run check` to check only the frontend. Shell wrappers are available as
`scripts/check.sh` and `scripts/check.ps1`.

GitHub Actions runs the same checks on Windows and Linux.

## Editor

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

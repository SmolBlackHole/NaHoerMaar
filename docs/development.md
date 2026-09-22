# Develop NaHörMaar

Parent: [Documentation index](README.md)

This guide covers local setup, Discord configuration and changes to the API or
database. The [engine API](engine-api.md) owns the public contract, and
[testing and acceptance](testing.md) owns checks and live listening evidence.
Python 3.12+ and Node.js 24.11+ with npm are required.

## Contents

- [Develop NaHörMaar](#develop-nahörmaar)
  - [Contents](#contents)
  - [Set up](#set-up)
  - [Configure Discord](#configure-discord)
  - [Start the services](#start-the-services)
    - [Restart and recovery](#restart-and-recovery)
  - [Update the frontend API contract](#update-the-frontend-api-contract)
  - [Daily bio](#daily-bio)
  - [Database changes](#database-changes)
  - [License inventory](#license-inventory)
  - [Test and verify](#test-and-verify)

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

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

## Configure Discord

Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
Environment variables override `.env` values. Invite the bot with the
`bot` and `applications.commands` scopes and View Channel, Connect and Speak
permissions for the voice channels you want to use.

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

Add a subset of those IDs to `admin_ids` to grant access to the live Logs page.
The log view holds only recent messages in memory and is cleared on restart.

Use `/pspsps` in Discord to bring the bot to your current voice channel. The
command checks this same whitelist on every call; a dashboard account is not
required. Its replies are visible only to you. Join a regular voice channel first;
the bot needs View Channel, Connect and Speak there. Calling it in the bot's
current channel leaves active playback alone. If playback was interrupted, it
resumes at the retained position; if there is no current track, queued work starts.
An explicit pause remains paused. Moving channels follows the same behavior as
the dashboard. A successful command replies `:3`.

The API runtime registers `/pspsps` globally during Discord login, so it is
available on the bot's servers without configuring guild IDs. Command
registration failures are logged without preventing music playback.

Sessions last seven days and survive backend restarts. Names and avatars belong
to the account and work across devices. An old browser profile can suggest a
name and avatar at first sign-in, but its past queue entries are not reassigned.
Signing out affects other tabs using that session and leaves music playing.

Appearance preferences are saved with the signed-in account. The **Cookies**
action in the sidebar opens the browser's YouTube consent settings. Covers and
video previews load only after consent; Discord audio works without it.

## Start the services

After setup and Discord configuration, start the backend from the repository
root in one terminal:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend
```

The [API explorer](http://127.0.0.1:8000/docs) describes the endpoints.
API calls require the authentication session cookie, and mutations also require
its CSRF token and configured origin. The dashboard retains the original
idempotency key and command body when checking a lost mutation response.

The server binds to `127.0.0.1:8000` and runs one bot instance. Keep one worker;
multiple workers would each start a bot and own a different player. Keep FastAPI
internal and route dashboard requests through Nuxt. `PUBLIC_ORIGIN` fixes the
allowed browser origin and OAuth redirect; forwarded host headers do not override
it. The dev command reads the root `.env`. A deployed Nuxt server needs the same
value in its environment, with HTTPS for secure cookies. Deployment remains a
separate step.

Start the dashboard in another terminal:

```powershell
npm run dev
```

Open `http://localhost:3012`. On PowerShell systems that block `npm.ps1`,
use `npm.cmd run dev`. Sign in to load the current session, queue and history.
The Nuxt server forwards requests to `http://127.0.0.1:8000`; set
`NUXT_BACKEND_URL` when using another port. Its route allowlist and discovery
parameters follow the [engine API](engine-api.md).

### Restart and recovery

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
interrupted track and position for a manual join. Coordinate a restart with
people listening in the channel.

## Update the frontend API contract

Public response models are defined in `backend/src/nahormaar_backend/engine/api_models.py`;
request models and routes live in `engine/api.py`. After changing them, run:

```powershell
npm run api:generate --workspace frontend
```

This exports OpenAPI in an isolated Python process and runs the locally installed
`openapi-typescript`. It does not enter the application lifespan, read runtime
configuration, connect to Discord or open the player database. Commit the resulting
`frontend/shared/api.generated.ts` with its API change. Do not edit or reformat
that generated file manually. `npm run api:check --workspace frontend` verifies
reproducibility and is included in the frontend/CI check command.

Use the named methods in `app/repositories/` for HTTP access. They are provided once
per Nuxt app by `app/plugins/repositories.ts`; obtain them through
`useRepositories()` in stores and local workflow composables. Components call
store/composable actions. Keep route strings out of spinners and toast decisions;
use semantic actions such as `queue.reordered` and `playback.seek`.

Stores own shared reactive state, not repositories. Keep search results and playlist
selection local to `useCatalog`/`useDiscovery`; only the radio preview is shared
between its entry points. The account repository uses the same transport as the
catalog and session. Do not add another fetch wrapper to the profile store.

Frontend tests inject repositories into a fresh Vue app and real Pinia using
`test/repository-fixture.ts`. Their fetch and EventSource implementations are
local doubles; they do not access the live bot, queue or account.

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

## License inventory

The Licenses page lists installed JavaScript and Python packages, fonts, artwork
and the selected FFmpeg build. Development startup and production builds refresh
the inventory; run `npm run licenses` to refresh it separately. Packages without
a bundled license text are marked and link to their source. Run setup first so
both the Python and Node.js dependencies are available.

## Test and verify

The [testing guide](testing.md) owns local checks, playback diagnostics and the
still-open live Discord acceptance. Checks with synthetic audio do not contact
the running bot; coordinate live listening tests separately.

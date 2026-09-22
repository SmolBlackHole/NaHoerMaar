# Develop NaHörMaar

Parent: [Documentation index](README.md)

This guide covers local setup, running the services and changes to the API or
database. [Discord setup](discord-setup.md) covers the Developer Portal,
bot installation, sign-in and the whitelist. The [engine API](engine-api.md)
owns the public contract, and [testing and acceptance](testing.md) owns checks
and live listening evidence.
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

Follow [Set up Discord](discord-setup.md) to create your own application,
install the bot, register the OAuth redirect and add Discord user IDs to
`access.toml`.
Copy `.env.example` to `.env` for the credentials. Environment variables
override `.env` values. Both local files stay out of Git.

The bot discovers its joined servers and voice channels automatically; no guild
ID is configured. `GET /api/channels` lists them with permissions. Select a
channel through `PUT /api/connection`, or use `/pspsps` in Discord. Changes to
the whitelist take effect without a restart; removing someone closes their
live updates within five seconds. The Logs page holds only recent messages in
memory and clears on restart.

Session and account storage are described in
[architecture](architecture.md#storage-and-access); profile and cookie controls
are covered in [listening together](listening.md#your-profile-and-browser).

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
value in its environment, with HTTPS for secure cookies. See
[Hosting considerations](hosting.md) before moving the services to an always-on
machine.

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
incoming track without replaying the outgoing tail. An active Radio resumes with
its original source and initiator. It keeps the tracks already in the queue and
fills any open places without adding those tracks again. A search interrupted by
the restart is retried. If the saved channel is gone or inaccessible, the
dashboard reports the issue and retains the interrupted track and position for
a manual join. Coordinate a restart with people listening in the channel.
The first upgrade from `engine_0001` cannot restore a Radio that was already
running before this change: that version did not save its source. Start that
Radio again after upgrading. Later restarts retain it.

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
Their combined metadata is owned by `engine/schema.py`. The Alembic revision
chain lives under `engine/migrations/versions/`.

`alembic.ini` targets `data/engine.sqlite3`, not the retired player database.
Use an isolated database by changing `sqlalchemy.url` before developing migrations:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe the change"
```

Review generated migrations. Startup initializes a fresh database atomically and
upgrades the known `engine_0001` revision to `engine_0002`, which adds Radio
state. It rejects older or unrelated schemas rather than replacing their data.
When introducing another engine revision, update the explicit startup policy and
its tests as part of the schema change.
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

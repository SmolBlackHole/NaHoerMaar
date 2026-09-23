# Develop NaHörMaar

Parent: [Documentation index](README.md)

This guide owns local setup, service startup and the few project-wide maintenance
commands that do not belong to one domain. Discord installation has its own
[setup guide](discord-setup.md). Database, frontend and playback changes link to
their technical owners instead of being explained here again.

Python 3.12+ and Node.js 24.11+ with npm are required.

## Table of contents

- [Develop NaHörMaar](#develop-nahörmaar)
  - [Table of contents](#table-of-contents)
  - [Set up](#set-up)
  - [Configure Discord](#configure-discord)
  - [Start the services](#start-the-services)
  - [Choose the owning guide](#choose-the-owning-guide)
  - [Daily bio](#daily-bio)
  - [License inventory](#license-inventory)
  - [Test and verify](#test-and-verify)
  - [Run the Linux CI precheck](#run-the-linux-ci-precheck)

## Set up

From the repository root:

```powershell
python scripts/dev.py setup
```

Linux and macOS can also use `./scripts/setup.sh`. If PowerShell blocks
`scripts/setup.ps1`, use the Python command above.

Setup installs a local FFmpeg binary through `imageio-ffmpeg` and checks that it
runs. You can override it with `FFMPEG_PATH`. On Linux and macOS, audio processing
also needs the system Opus library (`libopus0` on Debian/Ubuntu, `opus` through
Homebrew on macOS).

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

## Configure Discord

Follow [Set up Discord](discord-setup.md) to create your own application,
install the bot, register the OAuth redirect and add Discord user IDs to
`access.toml` for the owner and admins. Copy `.env.example` to `.env` for the
credentials. Environment
variables override `.env`; both local files stay out of Git.

Server, channel and member discovery, the absence of a configured guild ID and
access management belong to [Set up Discord](discord-setup.md). Runtime access checks,
revocation and the in-memory Logs endpoint belong to the
[Engine API](engine-api.md#authentication).

## Start the services

After setup and Discord configuration, start PostgreSQL and wait for it to become
healthy:

```powershell
docker compose up -d --wait database
```

Then start the backend from the repository root in one terminal:

```powershell
.venv\Scripts\python.exe -m nahormaar_backend
```

The server binds to `127.0.0.1:8000`. Its
[API explorer](http://127.0.0.1:8000/docs) shows the current routes. Keep one
worker: every backend process would otherwise start its own bot and player.
Keep FastAPI internal and route dashboard requests through Nuxt.

`PUBLIC_ORIGIN` fixes the browser origin and Discord OAuth redirect. Forwarded
host headers do not override it. The development command reads the root `.env`.
A deployed Nuxt server needs the same value in its environment, with HTTPS for
secure cookies. See [Hosting considerations](hosting.md) before exposing the
dashboard outside a local network.

Start the dashboard in another terminal:

```powershell
npm run dev
```

Open `http://localhost:3000`. On PowerShell systems that block `npm.ps1`, use
`npm.cmd run dev`. The Nuxt server forwards API requests to
`http://127.0.0.1:8000`; set `NUXT_BACKEND_URL` when the backend uses another
address.

Ctrl+C performs a clean backend shutdown: it drains accepted work, records the
latest checkpoint, stops audio and disconnects the bot. Coordinate restarts with
people in the voice channel. The exact resume behavior belongs to
[Playback](engine/playback.md#checkpoints-and-restart); database-file recovery is
documented separately in [Back up and restore NaHörMaar](recovery.md).

## Choose the owning guide

Use the guide for the boundary you are changing:

| Change | Read and update |
| --- | --- |
| Search, links, playlists, providers or metadata | [Catalog and metadata](engine/catalog.md) |
| Queue behavior or history | [Queue and history](engine/queue.md) |
| Radio refill and lifecycle | [Radio](engine/radio.md) |
| FSM, audio, crossfade, reconnect or restart | [Playback](engine/playback.md) |
| SQLAlchemy models, repositories or migrations | [Database](engine/database.md) |
| Repositories, stores, generated types or UI workflows | [Frontend architecture](frontend.md) |
| HTTP, SSE or public models | [Engine API](engine-api.md) |

After a public API change, regenerate the frontend contract as described in
[Frontend architecture](frontend.md#change-the-api-contract). Do not edit
`frontend/shared/api.generated.ts` manually.

## Daily bio

The bot sets its application description to a daily quote from
[quotes.toml](../quotes.toml). Add entries to the `de`, `en` and `nl` arrays under
`[quotes]`, with up to 400 characters per quote.

The selection stays the same throughout the host's local calendar day, including
after a restart, while the list remains unchanged. The bot checks for a new day
once a minute. List changes take effect the next day or after a restart. A failed
update leaves playback running and retries after a minute.

## License inventory

The Licenses page lists installed JavaScript and Python packages, fonts, artwork
and the selected FFmpeg build. Development startup and production builds refresh
the inventory. Run this command to update it separately:

```powershell
npm run licenses
```

Run setup first so both Python and Node.js dependencies are available. Packages
without bundled license text remain marked and link to their source.

## Test and verify

The [testing guide](testing.md) owns local checks, playback diagnostics and live
Discord acceptance. The complete local gate is:

```powershell
python scripts/dev.py check
```

Automated tests use isolated state and do not control the live bot. Optional
audio recordings consume additional CPU, and live Discord checks can interrupt
music; use the testing guide before running either.

## Run the Linux CI precheck

With Docker Desktop or Docker Engine running, execute the full gate in a clean,
disposable Linux container:

```powershell
python scripts/dev.py check-container
```

The command starts the disposable PostgreSQL test service, checks the working
tree, builds `Dockerfile.ci`, then runs the same `python scripts/dev.py check`
command used by GitHub Actions. Docker removes the gate container after the run
and retains the image as a build cache. The precheck mirrors the hosted Linux
job's database, toolchain and system Opus dependency.

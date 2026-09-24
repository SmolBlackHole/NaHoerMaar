# Host NaHörMaar

Parent: [Documentation index](README.md)

NaHörMaar ships one production path based on Docker Compose. It runs PostgreSQL,
one backend container for Discord and playback, and one Nuxt container for the
dashboard. FastAPI and PostgreSQL stay on the private Compose network. A reverse
proxy on the host publishes only the dashboard over HTTPS.

This guide gets a self-hosted instance running. It does not claim a minimum
machine size yet. Measure the actual host before relying on it for long sessions
or crossfade.

## Table of contents

- [Host NaHörMaar](#host-nahörmaar)
  - [Table of contents](#table-of-contents)
  - [What runs](#what-runs)
  - [Prepare the host](#prepare-the-host)
  - [Configure the instance](#configure-the-instance)
  - [Build and start](#build-and-start)
  - [Put the dashboard behind HTTPS](#put-the-dashboard-behind-https)
  - [Operate and update the instance](#operate-and-update-the-instance)
  - [Back up the database](#back-up-the-database)
  - [Choose a host](#choose-a-host)
  - [Measure capacity](#measure-capacity)

## What runs

The `database` service runs PostgreSQL 17 and owns the durable
`nahormaar-postgres` volume. The `backend` service owns the Discord connection
and shared player, and reaches PostgreSQL over the private Compose network. The
backend is deliberately not published on a host port. Its `/healthz` endpoint
reports whether HTTP is alive, while `/readyz` becomes healthy only after the
database, Discord and the engine have started.

The `frontend` service serves Nuxt on host loopback at port 3000 by default. It
forwards browser API requests to the backend over the Compose network. Your
reverse proxy is the only public entry point.

The `backup` service is a one-shot PostgreSQL maintenance command. It does not
start Discord or playback. Verified dumps are written to the host directory
`data/backups/`, separate from the live database volume.

Keep exactly one backend container and one Uvicorn worker. A second backend
would log in another bot process and compete for the same listening session.

## Prepare the host

Install Docker Engine with the Compose plugin, or Docker Desktop for a local
trial. Clone the repository and stay on the release or branch you intend to
run. The host does not need a separate Python, Node.js, FFmpeg or Opus setup;
the images contain those dependencies.

Create the two local configuration files before starting Compose:

```powershell
Copy-Item .env.example .env
Copy-Item config/access.example.toml config/access.toml
```

On Linux or macOS, use `cp` instead. Both files contain local credentials or
access policy and remain outside Git.

## Configure the instance

Follow [Set up Discord](discord-setup.md) to create an application, install its
bot and collect the user IDs for `config/access.toml`.

Set these values in `.env`:

```dotenv
DISCORD_TOKEN=replace-me
DISCORD_CLIENT_ID=replace-me
DISCORD_CLIENT_SECRET=replace-me
POSTGRES_DB=nahormaar
POSTGRES_USER=nahormaar
POSTGRES_PASSWORD=replace-with-a-long-random-secret
PUBLIC_ORIGIN=https://music.example.com
NAHORMAAR_PORT=3000
```

`PUBLIC_ORIGIN` is the address listeners open in their browser. Register this
exact Discord OAuth redirect:

```text
https://music.example.com/api/auth/discord/callback
```

Compose builds the internal `DATABASE_URL` from the PostgreSQL values and sets
container paths for `ACCESS_PATH` and `NODE_PATH`. The `DATABASE_URL` in
`.env` remains useful when the backend runs directly on the host. Leave the
container overrides alone unless you are also changing the deployment layout.

Port 3000 stays bound to `127.0.0.1`. Change `NAHORMAAR_PORT` when another local
service already uses it. Do not bind the port to every interface merely to get
HTTPS working; the reverse proxy can reach loopback directly.

## Build and start

Build both images, then start the services in the background:

```powershell
docker compose build
docker compose up -d
```

Docker Compose on Windows may reject a build context whose checkout path
contains non-ASCII characters. Moving the checkout to an ASCII-only path fixes
that Docker client error. You can also build the same named images directly and
let Compose start them without rebuilding:

```powershell
docker build --file docker/Dockerfile --target backend --tag nahormaar-backend:local .
docker build --file docker/Dockerfile --target frontend --tag nahormaar-frontend:local .
docker compose up -d --no-build
```

Inspect their state and startup logs:

```powershell
docker compose ps
docker compose logs --tail 100 backend frontend
```

The backend becomes healthy after the Discord gateway and engine are ready.
The frontend waits for that healthcheck before it starts. A missing token,
invalid OAuth origin or unavailable Discord connection therefore remains
visible in `docker compose ps` and the backend logs instead of producing a
half-ready dashboard.

Use `docker compose logs -f backend frontend` while diagnosing a live instance.
Stop following with Ctrl+C; that does not stop the containers.

The backend also writes `backend.log` to the `nahormaar-logs` Docker volume. It
starts a new file at midnight UTC and keeps the previous 14 files. These files
survive a container replacement, while the Logs page deliberately shows only
the latest 500 entries from the current backend process. User-triggered
operations include the Discord ID and profile name of the actor. Background
work is marked as a system event. API responses expose the same `X-Request-ID`
that appears as `trace_id` in Nuxt and backend logs. Copy it from the Logs page
to follow one action across the proxy, HTTP handler, Session and playback work.

The console stays at INFO while the rotating file also keeps DEBUG entries. To
inspect one trace inside the backend container:

```powershell
docker compose exec backend grep "trace_id=PASTE-ID-HERE" /app/data/logs/backend.log
```

Track attempts end with one `engine.audio.attempt_summary` line. It records the
provider media ID, preparation and attempt IDs, startup timing, played position,
underrun count, accumulated and longest stall, start mode and completion reason.
During a crossfade, outgoing and incoming attempts receive separate summaries.

Logs describe the operation and result without storing search text, media URLs,
tokens or OAuth callback query strings. Treat the files as operational data
anyway: Discord IDs and profile names identify the people using the instance.

## Put the dashboard behind HTTPS

Point a DNS name at the host and let a reverse proxy terminate TLS. With Caddy
running on the host, a minimal site block is:

```caddyfile
music.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

Reload Caddy, open the public address, and complete Discord sign-in. The domain
in Caddy, `PUBLIC_ORIGIN` and the OAuth redirect must agree. If a different
proxy already owns ports 80 and 443, configure its equivalent HTTP and
WebSocket reverse proxy to `127.0.0.1:3000`.

Do not publish backend port 8000. Nuxt is the browser-facing boundary and owns
the API proxy.

## Operate and update the instance

Compose restarts both services after a process failure or host reboot unless
you explicitly stop them. Useful commands are:

```powershell
docker compose stop
docker compose start
docker compose restart frontend
docker compose logs --since 10m backend
```

Coordinate a backend restart with listeners because it interrupts Discord
audio. The engine saves its checkpoint during a clean stop and restores the
session on startup.

To deploy a newer checkout:

```powershell
docker compose --profile maintenance run --rm backup
git pull --ff-only
docker compose build
docker compose up -d
docker compose ps
```

Read the logs after the update and check sign-in, the active channel, queue,
playback position and Radio state. `docker compose down` removes the containers
and network but keeps the named PostgreSQL volume. Do not add `--volumes` unless
you intend to delete the database. Managed dumps remain under `data/backups/`
until you remove them separately.

## Back up the database

Run the one-shot backup service at least once before treating the deployment as
recoverable:

```powershell
docker compose --profile maintenance run --rm backup
```

The [backup and restore guide](recovery.md#use-docker-compose) owns verification,
scheduling, copying backups off the host and the restore drill.

## Choose a host

A 64-bit Raspberry Pi or another always-on home server is a reasonable first
host. Reliable storage, cooling, power and upload bandwidth matter more than an
impressive idle benchmark. Friends outside the network also need a stable HTTPS
route to the dashboard.

A VPS avoids home-network exposure and power interruptions. Hetzner Cloud,
Hostinger and similar providers can run the same Compose file, but no particular
provider or plan is endorsed here. Shared CPU may be fine for ordinary playback
and still struggle when FFmpeg prepares an overlap.

## Measure capacity

Measure memory and per-core CPU while idle, during search and track start, and
through a crossfade. Watch for an audible stall or skip at the same timestamp.
Measure the Nuxt process separately and leave room for short-lived FFmpeg
processes.

The live procedure is in [Testing and acceptance](testing.md#live-acceptance).
A quiet development process or a successful image build is not evidence that a
small host can sustain playback.

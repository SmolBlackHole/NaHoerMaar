# Database

Parent: [Engine documentation](README.md)

NaHörMaar stores engine and account data in one PostgreSQL database. SQLAlchemy
owns the mappings and transaction boundaries. Alembic owns the schema history.
This page explains what is durable, how writes are grouped and how to change the
schema. Operational backups and restores belong in
[Back up and restore NaHörMaar](../recovery.md).

## Table of contents

- [Database](#database)
  - [Table of contents](#table-of-contents)
  - [One database, separate units of work](#one-database-separate-units-of-work)
  - [Stored engine state](#stored-engine-state)
  - [Repositories and transactions](#repositories-and-transactions)
  - [Schema ownership](#schema-ownership)
  - [Develop a migration](#develop-a-migration)
  - [Test database changes](#test-database-changes)
  - [Caches and temporary data](#caches-and-temporary-data)

## One database, separate units of work

`DATABASE_URL` selects the PostgreSQL database. Docker Compose builds the URL
from `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD`; a locally started
backend reads the complete URL from `.env`.

The database contains users, catalog data, discovery snapshots, the shared
listening session and listening facts. Sharing one database gives deployment and
recovery one durable unit. Each command still uses its own short unit of work.
Provider calls, FFmpeg work, Discord I/O and event delivery happen outside
database transactions.

## Stored engine state

The engine stores:

- the stable listening-session identity and settings;
- persistent tracks, media identities, artists and merged metadata;
- personal track reactions, playlist visibility, collaborators, linked source
  state, ordered occurrences and short-lived Undo receipts;
- ordered queue entries with request origin and a user reference;
- the current playback checkpoint and confirmed playback records;
- the active Radio run, candidates and exclusions;
- mutation receipts, outcomes and revision evidence used for idempotency;
- persisted background-job runs and operational incidents.

Tracks are the shared reference point. Queue entries and playback records refer
to them instead of copying complete metadata payloads. A queued occurrence and a
confirmed play remain separate records with their own IDs and attribution. Their
domain meaning lives in [Queue and history](queue.md), [Radio](radio.md) and
[Playback](playback.md).

User tables store Discord identities, profiles, preferences, browser sessions
and pending login attempts. Only hashes of browser-session tokens are stored.
Each user also stores the effective role and, for normal listeners, who granted
access and when. The immutable grant/revoke history lives beside those users in
PostgreSQL. Owner and admin IDs remain in `config/access.toml` as the operator
bootstrap boundary.

## Repositories and transactions

`database/core.py` owns the SQLAlchemy engine and session factory.
`database/uow.py` gives a command one explicit transaction. Feature repositories
own their private mappings under `users/`, `catalog/`, `library/`, `player`,
`listening/` and `operations/`; `statistics/` registers the same normalized
tables for read-only cross-module reports without owning a second write model.

Repositories flush inside the caller-owned unit of work and do not choose when
to commit. Player state, queue requests, receipts and revision changes therefore
commit together or roll back together. PostgreSQL enforces foreign keys and
transaction isolation; the application does not emulate a global writer lock.

Keep transactions short. Do not wait for a provider, audio source or Discord
while a transaction is open. Complete that work first, then return a correlated
result through the Session inbox.

## Schema ownership

The Alembic chain lives under `database/migrations/versions/`. The supported
head is `0018_playlist_owner_order`. Startup upgrades the configured
database before it starts the player or Discord gateway.

`0001_initial` creates the normalized user, catalog, player and listening
schema. `0002_catalog_search_indexes` adds catalog lookup indexes.
`0003_radio_request_attribution` records the initiating user on Radio-created
requests. Revisions `0004` through `0006` add the Discord avatar cache,
operational incidents and unattended playback state. Revisions `0007` through
`0010` add durable background-job history and the Catalog cleanup and source
revalidation jobs. `0011_player_settings_action` extends recorded Player
actions, and `0012_track_lyrics` adds the Lyrics cache.

`0013_track_reactions` adds one reaction per user and track.
`0014_personal_playlists` adds owner-scoped playlists and ordered entries with
stable track and optional source references. Catalog cleanup treats reactions
and playlist entries as durable references, so it cannot delete music still
used by the Library. `0015_playlist_sharing` adds visibility and collaborators,
`0016_linked_playlists` records imported playlist sources and synchronization
state, `0017_playlist_entry_undos` stores short-lived receipts that restore the
same playlist occurrence at its prior position, and
`0018_playlist_owner_order` persists the owner's explicit playlist-card order.
Applied migrations are immutable history. Add a new revision instead of editing
an applied one.

The runtime currently supports one shared listening session. Independent queues
per Discord server require an explicit schema and runtime change; the existing
session table does not provide that behavior by itself.

## Develop a migration

Alembic reads `DATABASE_URL`. Point it at an isolated development database or
test schema, then run:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe the change"
```

Review generated operations before keeping them. A schema change is complete
only when startup recognizes its predecessor, focused tests cover a fresh
database and the supported upgrade, and `alembic check` reports no drift.

Never generate or rehearse a migration against the live bot database. Use the
test service described below or another disposable PostgreSQL database.

## Test database changes

Start the disposable PostgreSQL service before local backend tests:

```powershell
docker compose --profile test up -d --wait database-test
python scripts/dev.py check
```

The test service listens only on `127.0.0.1:55432` and stores its data in
`tmpfs`. Tests create isolated schemas and remove only schemas bearing their own
prefix. The production database and the running bot are not used.

`python scripts/dev.py check-container` starts the same test service and runs the
complete gate in a clean Linux container. GitHub Actions supplies its own
PostgreSQL 17 service.

Rehearse operational recovery with a PostgreSQL dump before relying on it. The
exact commands are in [Back up and restore NaHörMaar](../recovery.md).

## Caches and temporary data

Discovery snapshots are persisted so a restart does not discard a pinned search
or playlist version. The catalog bounds their retention and may refresh stale
snapshots in the background. Playable media URLs, provider headers, FFmpeg
buffers, running tasks and the recent Logs buffer stay in memory because they
are valid only for the current process.

Linked Library playlists persist provider and playlist identity, the canonical
source URL, last attempt and success times, the last error code, unavailable
entry count and truncation flag. Their reusable Catalog tracks and preferred
sources remain durable, while temporary playable URLs are still resolved only
when Player needs them. A failed refresh updates source diagnostics without
discarding the last successful occurrence list.

[Catalog and metadata](catalog.md#cache-and-refresh-behavior) owns discovery
retention and refresh behavior. Durable playback and Radio restoration come from
normalized database state rather than temporary media material.

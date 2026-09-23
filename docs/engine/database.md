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
  - [Caches are not persistence](#caches-are-not-persistence)

## One database, separate units of work

`DATABASE_URL` selects the PostgreSQL database. Docker Compose builds the URL
from `POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD`; a locally started
backend reads the complete URL from `.env`.

The database contains the shared listening session and the Discord accounts
that may use the dashboard. Sharing one database gives deployment and recovery
one durable unit. Account updates still use their own short transactions and do
not join long playback operations.

The Session owns atomic engine changes. Account login, profile and appearance
operations use separate units of work. Provider calls, FFmpeg work, Discord I/O
and event delivery happen outside database transactions.

## Stored engine state

The engine stores:

- the stable listening-session identity and settings;
- persistent tracks, media identities, artists and merged metadata;
- ordered queue entries with origin and contributor snapshots;
- the current playback checkpoint and confirmed playback records;
- the active Manual or Radio strategy and Radio candidates;
- mutation receipts, outcomes and revision evidence used for idempotency.

Tracks are the shared reference point. Queue entries and playback records refer
to them instead of copying complete metadata payloads. A queued occurrence and a
confirmed play remain separate records with their own IDs and attribution. Their
domain meaning lives in [Queue and history](queue.md), [Radio](radio.md) and
[Playback](playback.md).

Account tables store OAuth accounts, browser sessions and pending login
attempts. Only hashes of browser-session tokens are stored. Each account also
stores its effective role and, for normal listeners, who granted access and
when. The immutable grant/revoke history lives beside those accounts in
PostgreSQL. Owner and admin IDs remain in `access.toml` as the operator
bootstrap boundary.

## Repositories and transactions

`engine/persistence.py` contains the engine mappings and repositories.
`persistence/models.py` and `persistence/accounts.py` own account, role and
session storage. `engine/schema.py` combines those mappings with the engine
metadata for startup and migrations.

Engine repositories run in caller-owned transactions and flush without choosing
when to commit. `write_transaction()` commits state, history, receipts and
revision changes together or rolls all of them back. PostgreSQL enforces foreign
keys and isolates concurrent transactions; the application does not emulate a
global writer lock.

Keep transactions short. Do not wait for a provider, audio source or Discord
while a transaction is open. Complete that work first, then return a correlated
result through the Session inbox.

## Schema ownership

The Alembic chain lives under `engine/migrations/versions/`. The supported head
is `engine_0004`, also named by `engine/schema.py`. Startup initializes an empty
database and its one listening session atomically. It upgrades the supported
`engine_0001`, `engine_0002` and `engine_0003` revisions in place, and rejects an
unknown or foreign schema without replacing it.

`engine_0002` added durable Radio source state. `engine_0003` widened Discord
channel IDs to PostgreSQL `BIGINT`, which is required for Discord snowflakes.
`engine_0004` added account roles, listener grant attribution and the durable
administration history.
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

## Caches are not persistence

Discovery snapshots and refresh work live in bounded in-memory caches. They may
disappear on restart without losing queued tracks or metadata already merged
into PostgreSQL. The separate Logs buffer is described by the
[diagnostics API](../engine-api.md#diagnostics).

[Catalog and metadata](catalog.md#cache-and-refresh-behavior) owns discovery
cache semantics. Durable playback and Radio restoration use database state, not
those caches.

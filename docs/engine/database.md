# Database

Parent: [Engine documentation](README.md)

NaHörMaar stores engine and account data in one SQLite database. SQLAlchemy owns
the mappings and transaction boundaries; Alembic owns schema versions. This page
explains what is durable, how writes are grouped and how to change the schema.
Operational copies and restores belong in [Back up and restore NaHörMaar](../recovery.md).

## Table of contents

- [Database](#database)
  - [Table of contents](#table-of-contents)
  - [One database, separate units of work](#one-database-separate-units-of-work)
  - [Stored engine state](#stored-engine-state)
  - [Repositories and transactions](#repositories-and-transactions)
  - [Schema ownership](#schema-ownership)
  - [Develop a migration](#develop-a-migration)
  - [Caches are not persistence](#caches-are-not-persistence)

## One database, separate units of work

`DATABASE_PATH` selects the SQLite file and defaults to `data/engine.sqlite3`.
It contains the shared listening session and the Discord accounts that may use
the dashboard. Sharing the file gives backup and deployment one durable unit; it
does not make account updates part of long playback transactions.

The Session owns atomic engine changes. Account login, profile and appearance
operations use short independent transactions. Provider calls, FFmpeg work,
Discord I/O and event delivery happen outside both kinds of transaction.

## Stored engine state

The engine stores:

- the stable listening-session identity and settings;
- persistent tracks, media identities, artists and merged metadata;
- ordered queue entries with origin and contributor snapshots;
- the current playback checkpoint and confirmed playback records;
- the active Manual or Radio strategy and Radio candidates;
- mutation receipts, outcomes and revision evidence used for idempotency.

Tracks are the shared reference point. Queue entries and playback records refer
to them instead of copying the complete metadata payload. A queue occurrence and
a confirmed play remain distinct rows with their own IDs and attribution. The
domain meaning of those records lives in [Queue and history](queue.md),
[Radio](radio.md) and [Playback](playback.md).

Account tables store OAuth accounts, browser sessions and pending login attempts.
Only hashes of session tokens are stored. The Discord whitelist remains in
`access.toml`; it is live authorization policy, not an account table.

## Repositories and transactions

`engine/persistence.py` contains the engine SQLAlchemy mappings and repositories.
`persistence/models.py` and `persistence/accounts.py` own account mappings and
account access. `engine/schema.py` combines both metadata sets for migration and
initialization.

Engine repositories run in caller-owned transactions and flush without deciding
when to commit. `write_transaction()` reserves SQLite's writer before reading,
so a read followed by a write cannot deadlock with another short mutation.
Foreign keys are enabled for every connection. The Session commits state,
history, receipts and revision changes together, then publishes events and runs
effects.

Do not hold a transaction open while waiting for a provider, audio source or
Discord. Complete that work outside the transaction and return a correlated
result through the Session inbox.

## Schema ownership

The Alembic chain lives under `engine/migrations/versions/`. The supported head
is named by `engine/schema.py`. Startup creates an empty database and its one
listening session atomically. It upgrades explicitly supported engine revisions
in place and rejects an old, unknown or foreign schema without replacing it.

The runtime supports exactly one listening session. Independent queues per
Discord server require a deliberate schema and runtime change; they are not
implicit in the existing session table.

Startup upgrades `engine_0001` to `engine_0002`. The older revision did not
store a Radio source, so a Radio that was active before that one-time upgrade
cannot be reconstructed and must be started again. Radio state created on
`engine_0002` survives subsequent restarts.

Applied migrations are immutable history. Never edit one to make a newer model
fit, and never point migration development at the live bot database. There is no
migration from the retired pre-engine player store.

## Develop a migration

`alembic.ini` targets `data/engine.sqlite3`. Change `sqlalchemy.url` to an
isolated database before generating or testing a migration, then run:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe the change"
```

Review the generated operations. A schema change is complete only when the
startup policy recognizes its predecessor, focused migration tests cover both a
fresh database and the supported upgrade, and the current schema still passes
`alembic check`.

Rehearse operational restore against another database path before relying on a
backup. The exact backup, verification and replacement commands are in
[Back up and restore NaHörMaar](../recovery.md).

## Caches are not persistence

Discovery snapshots and refresh work live in bounded in-memory caches. They can
disappear on restart without losing queued tracks or metadata already merged
into the database. The separate Logs buffer is described by the
[diagnostics API](../engine-api.md#diagnostics).

[Catalog and metadata](catalog.md#cache-and-refresh-behavior) owns discovery
cache semantics. Durable playback and Radio restoration use database state, not
those caches.

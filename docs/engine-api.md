# Engine API

Parent: [Architecture](architecture.md).

The normal backend entry point and local service now run this engine on a fresh
database. The frontend uses the native API for discovery, queue operations,
playback controls and live updates. The remaining Discord listening acceptance
is described under [verification](#verification). No legacy route or event-format
compatibility is promised.

## Startup and fresh data

`python -m nahormaar_backend` calls `engine/bootstrap.py:create_application`.
It loads configuration once and reserves the local API port before starting
Discord. The application lifespan then validates voice dependencies, initializes
the database and its single listening session, waits for Discord readiness,
opens engine services and registers `/pspsps`. Presence and daily bio updates
have their own tasks; they do not own playback state.

For the agreed fresh start, use `DATABASE_PATH=data/engine.sqlite3` in `.env`.
This is also the default when the variable is absent/empty. An explicit existing
path is respected, so an old `data/player.sqlite3` setting must be changed before
the coordinated restart. A legacy/foreign schema is rejected, never overwritten
or imported. Leave the old database in place as the rollback artifact.

The initializer creates the engine schema and one session atomically. Reopening
an engine database retains that session's identity, queue, checkpoint and settings.
Multiple listening sessions are rejected by this single-session composition.
The initial fresh start has no queue, history or accounts; sign in again to create
an account and configure preferences. `access.toml` and OAuth configuration are
unchanged. The old core and its optional copy importer have been removed.

Shutdown signals SSE subscribers before HTTP waits for open responses to finish.
The Session then saves measured playback intent/position and settles audio work;
providers, database and Auth close before the Discord gateway. Failure and
cancellation during startup also clean up owned tasks and resources.
An unexpected gateway exit terminates the engine lifespan and signals the HTTP
server to stop rather than leaving an unavailable API process behind.

## Ownership and composition

`engine/runtime.py:open_engine` takes an explicitly constructed `Auth`, provider
tuple, audio player, voice transport, listening-session ID and clock. It builds
the metadata store, catalog and Session against the authentication database path.
It requires schema `engine_0001`; it never reads `.env`, migrates an old database
or logs a Discord client in automatically. The caller establishes transport
readiness. HTTP uses `engine/api.py:create_app(runtime_factory, public_origin=...)`.

The composition owns the supplied resources. Session shutdown settles its inbox,
freezes audio and saves the measured checkpoint before closing effects. Catalog
tasks, adapters, providers, database and Auth then close. A combined audio/voice
adapter closes once. Partial startup cleans up already acquired resources.

`engine/commands.py:DiscordCommands` registers `/pspsps` on a supplied client.
It checks the same live whitelist, member voice channel and bot permissions,
then submits `Join` to the same Session as HTTP. `:3` follows confirmed connection,
not merely request acceptance. Registering commands or connecting a real client
is not part of the offline tests or automatic engine import.

## Schema management

The frozen Alembic chain lives under `engine/migrations/`. The normal entry point
uses `engine/schema.py:initialize(path)` to establish the schema and shared
listening session together. Explicit tooling can call `upgrade(connection)` or
use the root `alembic.ini`. See [database development](development.md#database-changes).
There is no old-data import or legacy API fallback.

## Authentication

The existing Discord OAuth/PKCE service, live whitelist, cookies, CSRF and
profile/preferences rules remain in use. Authentication routes remain:

- `GET /api/auth/discord` and `GET /api/auth/discord/callback`
- `GET /api/auth/session`, `POST /api/auth/logout`
- `PUT /api/profile`, `PUT /api/profile/appearance`

All other `/api/` requests need a valid session cookie. Mutations also require
the configured `Origin` and `X-CSRF-Token`. Replies are private and `no-store`.
Queue attribution comes from the authenticated account, never a request body.

## State and mutations

`GET /api/session` returns `session`, `queue`, `checkpoint`, `playback`, `history`,
`radio` and a `tracks` dictionary keyed by internal track ID. Entries and history
reference that dictionary; metadata is not independently copied into every row.
Channel and guild snowflakes are decimal strings at the HTTP boundary.

Every queue, playback, connection and radio mutation requires an
`Idempotency-Key` UUID. Responses contain `state`, `outcome` and `replayed`.
Outcomes include affected entries and actor information. The same key with a
different command or actor conflicts. Repeated accepted requests return the
original outcome and current state without applying another mutation.

| Endpoint | Body / meaning |
| --- | --- |
| `POST /api/queue` | `track_ids` in desired order (1..100), optional `skip_duplicates` |
| `DELETE /api/queue/{entry_id}` | Remove one occurrence |
| `PUT /api/queue/{entry_id}/position` | `before_entry_id` (null means end), `expected_queue_revision` |
| `POST /api/queue/clear` | `expected_queue_revision`, optional `contributor_id` |
| `POST /api/queue/undo/{undo_id}` | Restore within the existing 12-second deadline |
| `POST /api/playback/control` | `action`: play, pause, skip, stop or leave; optional `expected_attempt_id` |
| `PUT /api/playback/position` | `seconds`, required `expected_attempt_id` |
| `PUT /api/playback/volume` | `volume`, 0..1 |
| `PUT /api/playback/crossfade` | `seconds`, 0 or 3..7 |
| `GET /api/channels` | Available channels with server names and permissions |
| `PUT /api/connection` | `channel_id` |
| `POST /api/radio` | `seed` MediaReference, `expected_generation` (null for manual mode) |
| `POST /api/radio/{generation}/stop` | Stop automatic queue filling |
| `POST /api/radio/{generation}/retry` | Retry an exhausted/failed radio fetch |

Pause, skip and stop require the currently displayed attempt ID. Seek changes
that ID. The Session checks it inside its ordered transaction, so a delayed
control cannot affect a newer attempt. Play may omit it when there is no output.

HTTP success confirms the committed command, not successful external audio or
voice output. Subsequent states report resolving/starting/playing or connection
failure. History begins only after confirmed audio output.

Conflicts return 409, missing entries 404, invalid actions 422, unavailable
providers 502, and unavailable storage/runtime 503. Private provider URLs and
exception details are not returned.

## Discovery and stable selections

- `GET /api/catalog/search?q=...&provider=youtube_music&refresh=false`
- `POST /api/catalog/playlist` with `source_url`, optional `provider`/`refresh`
- `POST /api/catalog/track` with `source_url` and optional `provider`
- `GET /api/catalog/{search|playlist}/{version}?offset=0&limit=20`

Search defaults to Music. Explicit `youtube` selects Videos when registered.
Search and playlist observations remain bounded to 100 occurrences. Responses
carry `version`, `offset`, `total`, `entries` and `refresh`; playlist replies also
carry their title/reference. Each available result includes its persistent
`track_id`, source `position` and original finding. Unavailable rows have no track ID.

Clients select the track IDs from the displayed snapshot and submit them to
`POST /api/queue`. Repeated playlist occurrences may supply the same track ID
multiple times and create independent queue entries. This avoids interpreting
positions against a refreshed list, and accepted additions remain replayable
even after the discovery snapshot expires. Background refresh exposes a new
version without replacing the old visible ordering or changing a user's selection.

## Events

`GET /api/events` is SSE. Each connection first receives `event: state` containing
a complete current snapshot, including when `Last-Event-ID` is present. Later
`event: change` messages contain `action`, `outcome`, and committed `state`.
The event ID is the Session revision. This is a resynchronizing state stream,
not a durable activity log or a promise to replay every historical notification.

Subscriptions precede the initial snapshot read. Queue/history/checkpoint changes
publish only after commit. Failed transactions publish no success event. Slow
subscribers disconnect on overflow and resynchronize on reconnect. Authentication
is rechecked during idle periods and immediately before data delivery; revocation
emits `event: auth` and closes the stream. Shutdown closes subscriptions.

## Verification

The shared backend test isolation blocks real Discord login, external sockets
and the running dev-server ports, and uses temporary databases and synthetic
credentials. `test_engine_api.py` exercises the native ASGI app, including
authorization, account preferences and SSE logout/expiry/revocation.
`test_engine_commands.py` uses Discord interaction fixtures.
`test_bootstrap.py` covers initialization, rejected databases, the Alembic CLI,
Discord readiness/failure/cancellation, presence and restart through the actual
composition with controlled external dependencies.

The six optional `test_playback_pipeline.py` cases use real local FFmpeg/Opus and
recorded synthetic tones. Enable `NAHORMAAR_ENGINE_AUDIO_TESTS=1` only in a separately
agreed resource window. Passing offline tests does not replace the planned live
Discord listening acceptance, which remains a separate open step after cutover.

# Engine API and isolated migration

Parent: [Architecture](architecture.md). Implementation status: [rewrite plan](../refactoring.md).

This describes the parallel engine, not the currently deployed backend. The
frontend still uses the old API. No legacy route or event-format compatibility
is promised by the new engine. Live cutover is a separately coordinated step.

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

## Copy migration

`engine/migration.py:migrate_copy(source, destination, now=...)` is an explicit
offline operation. Supply a consistent copy with legacy Alembic revision `0011`,
an absent destination path and a timezone-aware migration timestamp. The source
opens read-only. The destination is created exclusively; failure removes the
incomplete destination. An existing destination is never overwritten.

The new schema has its own frozen Alembic history under `engine/migrations/`.
The old application's migration chain does not discover or execute it. For a
fresh empty engine database, call `engine/schema.py:upgrade` with an explicit
SQLAlchemy connection. Migrating user data uses `migrate_copy`, not `upgrade` alone.

Migration preserves queue occurrence IDs and order, history IDs, account IDs,
contributor snapshots, account preferences, login/session data, operation IDs,
fingerprints, undo anchors/deadlines, revisions, channel, volume, crossfade,
position and playing/paused intent. Pending old receipts become interrupted,
matching old recovery behavior. Imported receipts reserve their original IDs;
they are not an API that executes old commands. Their original result payload,
single-entry ID and HTTP status are retained as migration evidence, separate
from the engine's domain outcome.

URL aliases share one track. Duplicate queue/playlist occurrences remain separate.
Known metadata is merged without fabricating artist identities from display names.
An unknown source retains its exact reference under the `legacy` namespace and
is counted in the migration report. Conflicting known identities abort migration.
No provider is contacted and no temporary stream URL is extracted or stored.

Old history provides confirmed start times, not end measurements. Unknown ends
stay unknown. A confirmed current checkpoint must match the latest history record;
its logical play ID survives restoration. Unconfirmed current playback keeps no
play ID until output is confirmed. An inconsistent checkpoint aborts migration
rather than guessing and accidentally adding another listen.

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

All engine tests run with plugin autoload disabled, the engine-only confcutdir and
a fresh temporary database directory. `test_migration.py` uses constructed legacy
databases. `test_engine_api.py` exercises the ASGI app with controlled providers
and transports; `test_engine_commands.py` uses Discord interaction fixtures.
They do not use the running service, active queue, live credentials or Discord.

The six optional `test_playback_pipeline.py` cases use real local FFmpeg/Opus and
recorded synthetic tones. Enable `NAHORMAAR_ENGINE_AUDIO_TESTS=1` only in a separately
agreed resource window. Passing offline tests does not replace the planned live
Discord listening and restart acceptance before cutover.

# Engine API

Parent: [Documentation index](README.md)

The backend and dashboard use this API for discovery, queue operations, playback
controls and live updates. This page owns the HTTP/SSE contract. Runtime
ownership is documented in [architecture](architecture.md). The engine pages own
[catalog](engine/catalog.md), [queue](engine/queue.md), [Radio](engine/radio.md),
[playback](engine/playback.md) and [database](engine/database.md) behavior behind
that contract. A real Discord listening check remains
[open](testing.md#live-acceptance).

## Table of contents

- [Engine API](#engine-api)
  - [Table of contents](#table-of-contents)
  - [Authentication](#authentication)
  - [State and mutations](#state-and-mutations)
  - [Discovery and stable selections](#discovery-and-stable-selections)
  - [Events](#events)
  - [Diagnostics](#diagnostics)
  - [Verification](#verification)

## Authentication

Discord OAuth with PKCE, role-based access, session cookies and CSRF protect the
API. Login attempts are browser-bound and single-use. Account sessions survive
backend restarts, and profile and appearance preferences belong to the signed-in
account.
Authentication routes are:

- `GET /api/auth/discord` and `GET /api/auth/discord/callback`
- `GET /api/auth/session`, `POST /api/auth/logout`
- `PUT /api/profile`, `PUT /api/profile/appearance`

All other `/api/` requests need a valid session cookie. Mutations also require
the configured `Origin` and `X-CSRF-Token`. Replies are private and `no-store`.
Queue attribution comes from the authenticated account, never a request body.

`GET /api/auth/session` includes the Discord ID and the effective `owner`,
`admin` or `user` role. Operator roles come from `config/access.toml`; ordinary
listener roles and their grant attribution live on the account in PostgreSQL.
Revoking a listener clears that role and also removes their active sessions.

Owners and admins use these administration routes:

| Endpoint | Meaning |
| --- | --- |
| `GET /api/admin/access` | Operators, listener grants and recent access history |
| `PUT /api/admin/access/{discord_id}` | Grant listener access |
| `DELETE /api/admin/access/{discord_id}` | Revoke listener access and sessions |
| `GET /api/admin/access/members` | Cached Discord members with guild details |

The owner may revoke any normal grant. An admin may revoke only a grant created
by that admin. The backend enforces this rule. Owner and admin roles cannot be
changed through the API.

## State and mutations

`GET /api/session` returns `session`, `queue`, `checkpoint`, `playback`, `history`,
`radio` and a `tracks` dictionary keyed by internal track ID. Entries and history
reference that dictionary; metadata is not independently copied into every row.
Channel and guild snowflakes are decimal strings at the HTTP boundary.
The public models live in `engine/api_models.py`. Playback exposes a connection
state, not internal connection or preparation tokens. Track responses contain
identity, source URL and metadata, without storage timestamps or provenance.

Every queue, playback, connection and radio mutation requires an
`Idempotency-Key` UUID. Responses contain `request_id`, `action`, `state`,
`outcome` and `replayed`. The semantic action (for example `queue.removed` or
`playback.seek`) is shared with SSE. Outcomes include affected entries and actor
information. `state.tracks` also supplies metadata for outcome entries that have
already left the queue, including replayed removals. The same key with a
different command or actor conflicts. Repeated accepted requests return the
original outcome and current state without applying another mutation.
Once the Session accepts a command, cancellation of its HTTP caller does not
cancel the committed work. A client retry keeps the same idempotency key.

Nuxt gives every API request an `X-Request-ID`. The backend accepts a valid UUID
or replaces an invalid value, returns the final ID in the response header and
uses it throughout the request, Session command and resulting playback work.
This trace ID follows one technical path through the system. It is separate
from the `Idempotency-Key`, which identifies a mutation for replay protection.

| Endpoint | Body / meaning |
| --- | --- |
| `POST /api/queue` | `track_ids` in desired order (1..100), optional `skip_duplicates` |
| `DELETE /api/queue/{entry_id}` | Remove one occurrence |
| `PUT /api/queue/{entry_id}/position` | `before_entry_id` (null means end), `expected_queue_revision` |
| `POST /api/queue/clear` | `expected_queue_revision`, optional `contributor_id` |
| `POST /api/queue/undo/{undo_id}` | Restore within the queue's existing Undo deadline |
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
Errors outside a committed command use `{code, message, retryable}`. A rejected
command may return its mutation envelope with a non-`ok` outcome and current state.

## Discovery and stable selections

- `GET /api/catalog/search?q=...&provider=youtube_music&refresh=false`
- `POST /api/catalog/playlist` with `source_url`, optional `provider`/`refresh`
- `POST /api/catalog/track` with `source_url` and optional `provider`
- `GET /api/catalog/{search|playlist}/{version}?offset=0&limit=20`

Search defaults to Music. Explicit `youtube` selects Videos when registered.
Search and playlist observations remain bounded to 100 occurrences. Responses
carry `version`, `offset`, `total`, `next_offset`, `source_has_more`, `entries`,
`playlist`, `error` and `refresh`. `next_offset` paginates the pinned snapshot;
`source_has_more` reports an upstream continuation beyond its bounded contents.
Every playlist version includes `playlist: {title, reference}`, also after a
refresh; search responses set it to null. Each result contains `track_id`,
source `position`, nullable `reference`, `metadata` and `unavailable`.
Unavailable rows have no track ID and may have no reference. There is no
background preview job to create or cancel.

Clients select the track IDs from the displayed snapshot and submit them to
`POST /api/queue`. Repeated playlist occurrences may supply the same track ID
multiple times and create independent queue entries. This avoids interpreting
positions against a refreshed list, and accepted additions remain replayable
even after the discovery snapshot expires. Background refresh exposes a new
version without replacing the old visible ordering or changing a user's selection.

A stale cached version can be shown while one shared refresh runs.
`refresh=true` requests a fresh observation without moving the visible
selection; an unchanged source keeps its version. A failed refresh leaves the
last known version available. Pagination stays within the observed snapshot; it
does not fetch beyond that limit. Freshness, retention and
process lifetime belong to [Catalog and metadata](engine/catalog.md#cache-and-refresh-behavior).

## Events

`GET /api/events` is SSE. Each connection first receives `event: state` containing
a complete current snapshot, including when `Last-Event-ID` is present. Later
`event: change` messages contain `request_id`, `action`, `outcome`, and committed
`state`. HTTP and SSE report the same request ID for a command. Clients deduplicate
by that ID regardless of arrival order. Internal state updates use `session.updated`;
they do not imply a listener requested a queue edit.
The event ID is the Session revision. This is a resynchronizing state stream,
not a durable activity log or a promise to replay every historical notification.

Subscriptions precede the initial snapshot read. Queue/history/checkpoint changes
publish only after commit. Failed transactions publish no success event. Slow
subscribers disconnect on overflow and resynchronize on reconnect. Authentication
is rechecked during idle periods and immediately before data delivery; revocation
emits `event: auth` and closes the stream. Shutdown closes subscriptions.

## Diagnostics

`GET /api/diagnostics/logs` returns up to 200 recent bot log entries. Only
owner and admin accounts may call it; other authenticated users receive
403. `?after={id}` returns newer entries for the Logs page. The server keeps at
most 500 entries in memory and discards that view on restart. Each entry also
contains `actor_id` and `actor_name` when an authenticated user caused the
operation. Background work leaves both fields empty. `trace_id` connects the
Nuxt proxy request, HTTP handler, Session command and playback effects where a
single action caused them. The Logs page can filter by trace, actor, source or
message and copies the full trace ID from its shortened display.

The endpoint does not expose the log files or provide a durable audit history.
The backend writes its own operational log to `data/logs/backend.log` as well.
That file rotates at midnight UTC and keeps 14 rotated files. Search text,
media URLs, tokens and OAuth callback query strings stay out of these logs.
The Nuxt proxy records method, path, status, duration, origin rewriting and
client aborts. It logs only the URL path, never its query string, request body,
cookies or headers.

## Verification

The backend API, command and bootstrap tests exercise authorization, account
preferences, SSE, Discord interaction fixtures, schema initialization and restart
with isolated dependencies. The six optional audio recordings use local FFmpeg/Opus
and synthetic tones. Recorded CI evidence and the still-open live Discord check
belong in [testing and acceptance](testing.md#live-acceptance).

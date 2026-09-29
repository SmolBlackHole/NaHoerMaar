# Engine API

Parent: [Documentation index](README.md)

The backend and dashboard use this API for discovery, queue operations, playback
controls and live updates. This page owns the HTTP/SSE contract. Runtime
ownership is documented in [architecture](architecture.md). The engine pages own
[catalog](engine/catalog.md), [queue](engine/queue.md), [Radio](engine/radio.md),
[playback](engine/playback.md) and [database](engine/database.md) behavior behind
that contract. The live Discord check is documented under
[testing](testing.md#live-acceptance).

## Table of contents

- [Engine API](#engine-api)
  - [Table of contents](#table-of-contents)
  - [Authentication](#authentication)
  - [Process health](#process-health)
  - [State and mutations](#state-and-mutations)
  - [Playback history](#playback-history)
  - [Library](#library)
  - [Discovery and stable selections](#discovery-and-stable-selections)
  - [Lyrics](#lyrics)
  - [Events](#events)
  - [Operations](#operations)
  - [Verification](#verification)

## Authentication

Discord OAuth with PKCE, role-based access, session cookies and CSRF protect the
API. Login attempts are browser-bound and single-use. Browser sessions survive
backend restarts, and profile and appearance preferences belong to the signed-in
user.
Authentication routes are:

- `GET /api/auth/discord` and `GET /api/auth/discord/callback`
- `GET /api/auth/session`, `POST /api/auth/logout`
- `GET /api/users/me`, `PATCH /api/users/me`
- `GET /api/profiles/me`, `GET /api/profiles/{user_id}`
- `GET /api/statistics`

The PATCH document accepts optional nested `profile` and `appearance` objects
and requires at least one of them. It updates both atomically when both are
present. The cheap user document contains account state only. Profile routes
combine that identity with recent listening and personal statistics for the
selected period. `GET /api/statistics` returns the group report. Statistics use
`7d`, `30d`, `year` or `all` and return display-ready daily or monthly activity
buckets.

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
| `GET /api/access` | Operators, listener grants and recent access history |
| `PUT /api/access/{discord_id}` | Grant listener access |
| `DELETE /api/access/{discord_id}` | Revoke listener access and sessions |
| `GET /api/access/members?q=...&guild_id=...` | Cached Discord members with optional text and guild filters |

The owner may revoke any normal grant. An admin may revoke only a grant created
by that admin. The backend enforces this rule. Owner and admin roles cannot be
changed through the API.

## Process health

`GET /health` reports `{ "status": "alive" }` as soon as the HTTP process can
answer. `GET /ready` additionally checks PostgreSQL, the restored Player and
Listening services, and, when Discord is enabled, the gateway and playback
coordinator. It returns `200` only when every enabled component is ready and
otherwise returns `503` with the individual component states. These two
container probes are deliberately outside the authenticated `/api/` contract
and the generated OpenAPI schema.

## State and mutations

`GET /api/player` returns the complete Player projection: Session and queue
revisions, settings, Queue, checkpoint, Radio and the current playback and voice
runtime. Queue and current requests include their canonical Track and known
requester. The Player module builds this projection once; ordinary reads,
mutation replies and SSE serialize that same read model. Channel and guild
snowflakes are decimal strings at the HTTP boundary. Playback exposes public
phases and stable error codes, without internal connection or preparation
tokens.

Every queue, playback, voice and Radio mutation requires an `Idempotency-Key`
UUID header. Responses contain `operation_id`, `player`, `outcome` and
`replayed`. The semantic action (for example `queue.removed` or
`playback.seeked`) is shared with SSE. Outcomes include counts, affected entry
IDs and optional Undo information. The same key with a different command or
actor conflicts. Repeated accepted requests return the original outcome and
current Player projection without applying another mutation.
Once the Session accepts a command, cancellation of its HTTP caller does not
cancel the committed work. A client retry keeps the same idempotency key.

Nuxt gives every API request an `X-Request-ID`. The backend accepts a valid UUID
or replaces an invalid value, returns the final ID in the response header and
uses it throughout the request, Session command and resulting playback work.
This trace ID follows one technical path through the system. It is separate
from the `Idempotency-Key`, which identifies a mutation for replay protection.

| Endpoint | Body / meaning |
| --- | --- |
| `GET /api/player` | Complete current Player projection |
| `POST /api/player/queue` | `tracks` in desired order (1..100), optional `skip_duplicates` |
| `DELETE /api/player/queue/{entry_id}` | Remove one occurrence at `expected_queue_revision` |
| `PATCH /api/player/queue/{entry_id}` | Move before `before_entry_id`, or to the end when null |
| `POST /api/player/queue/clear` | Clear at `expected_queue_revision`, optionally for one `requested_by` User ID |
| `POST /api/player/queue/undo` | Restore `undo_id` within the Queue's existing Undo deadline |
| `POST /api/player/control` | `action`: play, pause, skip, stop or seek; only seek also accepts `seconds` |
| `PATCH /api/player` | Update `volume` and/or `crossfade_seconds` atomically |
| `PUT /api/player/sleep-timer` | Set the remaining `seconds`, from 60 to 86400 |
| `DELETE /api/player/sleep-timer` | Cancel the active sleep timer |
| `GET /api/player/voice/channels` | Available channels with server names and permissions |
| `PUT /api/player/voice` | Join or move to `channel_id` |
| `DELETE /api/player/voice` | Leave voice |
| `PUT /api/player/radio` | Start or replace Radio from `seed` and optional `expected_generation` |
| `DELETE /api/player/radio` | Stop the matching `expected_generation` |
| `POST /api/player/radio/retry` | Retry the matching exhausted or failed generation |

Volume accepts 0..1. Crossfade accepts 0 or 3..7 seconds. Queue revisions and
Radio generations prevent stale writes from changing newer state. The ordered
Player Session applies the same checks for HTTP and internal commands.

HTTP success confirms the committed command, not successful external audio or
voice output. Subsequent states report resolving/starting/playing or connection
failure. History begins only after confirmed audio output.

Conflicts return 409, missing entries 404, invalid actions 422, unavailable
providers 502, and unavailable storage/runtime 503. Private provider URLs and
exception details are not returned.
Errors outside a committed command use `{code, retryable}`. A rejected
command may return its mutation envelope with a non-`ok` outcome and current state.

## Playback history

`GET /api/playbacks` is the single collection of confirmed playback starts.
The Queue requests its first ten items, while the History page uses the same
resource with numbered navigation. Repeated starts of the same track remain
separate items.

The collection accepts these optional filters:

- `q` searches track titles and artist names case-insensitively;
- `radio=true` returns Radio requests, while `radio=false` excludes them;
- `requested_by` filters by the requesting User ID;
- `started_from` and `started_to` are inclusive timezone-aware boundaries;
- `end_reason` is `completed`, `skipped`, `stopped` or `failed`.

`page` and `page_size` select a numbered page. The first response returns an
opaque `snapshot`; later pages send that value back so newly started playback
cannot shift or duplicate existing results. Changing search text or filters
starts a new snapshot. The response uses `items`, `page`, `page_size`, `total`,
`page_count` and `snapshot`, plus the known requesters used by the History
filter.

## Library

Library routes store reactions and private personal playlists for canonical
Catalog tracks. All routes require an authenticated browser session. Ordinary
mutation protection from [Authentication](#authentication) applies.

Reaction routes are:

| Endpoint | Body / meaning |
| --- | --- |
| `GET /api/library/reactions?track_id=...` | Summaries for 1..100 track IDs, including the current user's value and totals |
| `PUT /api/library/tracks/{track_id}/reaction` | Set or replace `{ "value": "like" }` or `dislike` |
| `DELETE /api/library/tracks/{track_id}/reaction` | Remove the current user's reaction |
| `GET /api/library/tracks?reaction=like` | Searchable, numbered Liked or Disliked collection |
| `GET /api/library/tracks/{track_id}/reactions` | Numbered participants for `like` or `dislike` |

Playlist routes are:

| Endpoint | Body / meaning |
| --- | --- |
| `GET /api/library/playlists` | Searchable, numbered personal playlists |
| `POST /api/library/playlists` | Create a playlist with `name` |
| `GET /api/library/playlists/{playlist_id}` | Read one owner-scoped playlist |
| `PATCH /api/library/playlists/{playlist_id}` | Rename with `name` and `expected_revision` |
| `DELETE /api/library/playlists/{playlist_id}` | Delete at `expected_revision` |
| `POST /api/library/playlists/{playlist_id}/duplicate` | Copy at `expected_revision`, with an optional new `name` |
| `GET /api/library/playlists/{playlist_id}/entries` | Searchable, numbered ordered occurrences |
| `POST /api/library/playlists/{playlist_id}/entries` | Add 1..100 track selections at `expected_revision` |
| `DELETE /api/library/playlists/{playlist_id}/entries/{entry_id}` | Remove one occurrence at `expected_revision` |
| `PUT /api/library/playlists/{playlist_id}/order` | Replace the complete entry-ID order at `expected_revision` |
| `POST /api/library/playlists/{playlist_id}/queue` | Queue the saved order and duplicates at `expected_revision` |

Collections use `q`, `page`, `page_size` and an opaque `snapshot` where
applicable. They return the same `items`, `page`, `page_size`, `total`,
`page_count` and `snapshot` document as playback history. Playlist-entry
snapshots encode the playlist revision, so pages from different orders cannot
be combined.

Playlist mutations increment `revision`. A stale `expected_revision` returns
409 with `library_playlist_revision_conflict`. Missing tracks, sources,
playlists or entries and invalid full-order submissions use stable `library_*`
error codes. A playlist holds at most 100 occurrences. Queueing a playlist also
requires an `Idempotency-Key` and returns the ordinary Player mutation envelope.
The [Library guide](engine/library.md) owns the domain and persistence rules.

## Discovery and stable selections

- `GET /api/catalog/search?q=...&provider=youtube_music&refresh=false`
- `GET /api/catalog/playlist?url=...` with optional `provider` and `refresh`
- `GET /api/catalog/link?url=...` with an optional `provider`
- `GET /api/catalog/discoveries/{version}?offset=0&page_size=20`
- `POST /api/catalog/discoveries/{version}/continuations?offset=...&page_size=20`

Search defaults to Music. Explicit `youtube` selects Videos when registered.
Search and playlist observations remain bounded to 100 occurrences. Responses
carry `version`, `offset`, `page_size`, `total`, `next_offset`,
`source_has_more`, `items`,
`stale` and `refreshing`. Playlist responses additionally carry `source_url`
and `playlist_title`; search responses carry the normalized query.
`next_offset` paginates the pinned snapshot, while `source_has_more` reports an
upstream continuation beyond its bounded contents. Creating a continuation does
bounded provider work and returns the requested page from the new immutable
version. Each entry contains its stable position, canonical track and selected
provider source.

Clients select the track IDs from the displayed snapshot and submit them to
`POST /api/player/queue`. Repeated playlist occurrences may supply the same
track ID multiple times and create independent queue entries. This avoids
interpreting positions against a refreshed list, and accepted additions remain
replayable even after the discovery snapshot expires. Background refresh
exposes a new version without replacing the old visible ordering or changing a
user's selection.

A stale cached version can be shown while one shared refresh runs.
`refresh=true` requests a fresh observation without moving the visible
selection; an unchanged source keeps its version. A failed refresh leaves the
last known version available. Pagination stays within the observed snapshot; it
does not fetch beyond that limit. Freshness, retention and
process lifetime belong to [Catalog and metadata](engine/catalog.md#cache-and-refresh-behavior).

## Lyrics

`GET /api/tracks/{track_id}/lyrics` reads lyrics for one canonical Catalog
track. `refresh=true` bypasses a fresh cache entry. The response states are
`available`, `instrumental` and `not_found`; available lyrics contain ordered
lines with optional start and end seconds and report whether synchronization is
available. Provider attribution, cache freshness and stale fallback are explicit
response fields.

A missing Catalog track returns `404`. LRCLIB transport, rate-limit or response
failures return `502` unless a successful cached copy for the same track
metadata can be served as stale. The endpoint is a track-keyed read and is not
part of Player SSE. Matching, retention and fallback behavior belong in
[Lyrics](engine/lyrics.md).

## Events

`GET /api/events` is SSE. Each connection first receives `event: state` containing
a complete current snapshot, including when `Last-Event-ID` is present. Later
`event: change` messages contain `message_id`, `correlation_id`, optional
`causation_id`, `operation_id`, `action`, `outcome`, and committed `state`.
HTTP and SSE report the same operation ID for a command. Clients deduplicate by
that ID regardless of arrival order. The message identifiers preserve causal
tracing across internal work. Runtime-only state updates arrive as `event: state`;
they do not imply a listener requested a queue edit.
The event ID is the Session revision. This is a resynchronizing state stream,
not a durable activity log or a promise to replay every historical notification.

Subscriptions precede the initial snapshot read. Queue/history/checkpoint changes
publish only after commit. Failed transactions publish no success event. Slow
subscribers disconnect on overflow and resynchronize on reconnect. Authentication
is rechecked during idle periods and immediately before data delivery; revocation
emits `event: auth` and closes the stream. Shutdown closes subscriptions.

## Operations

Every Operations endpoint requires an owner or admin account. Other
authenticated users receive `403`.

`GET /api/jobs` returns the registered jobs and their current state.
`POST /api/jobs/{job_id}/runs` accepts one bounded manual run. Persisted run
history is available through `GET /api/jobs/runs`, ordered newest first with
`page_size`, `cursor`, `job_id` and `status` queries. Its response uses the
shared cursor document. `GET /api/jobs/runs/{run_id}` returns the complete
details for one run.

`GET /api/incidents` combines a full-period summary with one numbered page of
matching incident items. `period`, `severity`, `component`, `code` and
`actor_id` filter both the summary and the page. `page` and `page_size` select
the visible items. The first response returns an opaque `snapshot`; later pages
send it back so new Incidents cannot shift the report window, totals or item
offsets. Changing filters or explicitly refreshing starts a new snapshot.
Changing pages alone does not change totals, common error counts, affected
operations or associated-user counts.

`GET /api/logs` reads the bounded in-memory process log. `after` and `limit`
support incremental polling. `q`, `level`, `source`, `actor_id`, `request_id`,
`correlation_id` and `causation_id` are applied before `limit`, so an older
matching entry remains searchable while it is retained. The process keeps at
most 500 entries and discards this in-memory view on restart. An initial read
returns the latest matching window. Reads with `after` return the oldest
retained matches after that cursor so repeated polls drain a backlog in order.

The Logs endpoint does not expose log files or provide a durable audit history.
The backend also writes its operational log to `data/logs/backend.log`. That
file rotates at midnight UTC and keeps 14 rotated files. Search text, media
URLs, tokens and OAuth callback query strings stay out of these logs. The Nuxt
proxy records method, path, status, duration, origin rewriting and client
aborts. It logs only the URL path, never its query string, request body, cookies
or headers.

## Verification

The backend API, command and bootstrap tests exercise authorization, account
preferences, SSE, Discord interaction fixtures, schema initialization and restart
with isolated dependencies. The six optional audio recordings use local FFmpeg/Opus
and synthetic tones. Recorded CI evidence and the still-open live Discord check
belong in [testing and acceptance](testing.md#live-acceptance).

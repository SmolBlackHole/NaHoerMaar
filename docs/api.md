# Control API

Parent: [Development guide](development.md)

The local API runs at `http://127.0.0.1:8000`. Its interactive reference is at
`/docs`, with an OpenAPI schema at `/openapi.json`.

## Sign in and keep access

Open `GET /api/auth/discord` through the dashboard origin to sign in. Discord
returns to `/api/auth/discord/callback`; the backend checks the browser-bound,
single-use login state and the Discord ID whitelist before issuing a session.

All other `/api` routes require the `nahormaar_session` cookie, including search,
playlist previews and SSE. It is HTTP-only, SameSite=Lax, secure on HTTPS and
expires seven days after sign-in. `GET /api/auth/session` returns `profile`,
`profile_complete`, `appearance`, `csrf_token` and the Unix timestamp `expires_at`.

For every mutation, send `X-CSRF-Token` from that response and an `Origin` equal
to `PUBLIC_ORIGIN`. `PUT /api/profile` accepts `name` (1 to 32 characters) and an
`avatar` from the Pixabot catalog, then returns the updated session response.
`POST /api/auth/logout` returns 204 and invalidates the session without stopping
playback. These routes do not need an idempotency key.

`PUT /api/profile/appearance` saves the signed-in user's display preferences and
returns the saved values. Send `mode`, `artworkColors`, `primaryColor`,
`neutralColor`, `fontFamily`, `iconSet` and `textSize`. The OpenAPI schema lists
the allowed choices. This route requires CSRF protection but no idempotency key.

Missing or expired sessions return 401 (`signed_out`). Unlisted accounts return
403 (`access_denied`); a missing or invalid whitelist returns 503
(`access_unavailable`). Session storage failures return 503 (`auth_unavailable`).
Clients must clear private state and stop live updates when access is lost.

## Read state

`GET /api/state` returns the current track, upcoming entries, playback and voice
states, channel ID, volume, playback ID, progress anchors and any playback issue.
Titles, artists, uploader links, duration and thumbnails arrive asynchronously;
unavailable fields remain null. Discord channel IDs are strings; queue and
playback IDs are UUIDs.

Each entry includes `added_by`, either null or the contributor's `id` (UUID),
`name` (1 to 32 characters) and `avatar` (four hexadecimal characters). This field
appears only in responses: the backend assigns it from the authenticated account.
Supplying `added_by` when adding tracks is rejected. The profile is saved with
the entry and its playback history; later profile edits leave those snapshots
unchanged. Requeuing records the person adding it again. Legacy contributors,
including null attribution, remain intact and are not linked to new accounts.

`recently_played` contains up to 100 starts, newest first. Each item has its own
`id`, a timezone-aware `played_at` timestamp and an `entry` with track metadata.
Skipped tracks remain in history; unplayed removals do not enter it. Pause/resume
and seeking or automatic stream retries do not add another start. Requeue a history item
through `POST /api/queue` with its source URL.

`GET /api/channels` lists voice channels from all available servers the bot has
joined, with `can_connect` and `can_speak` flags. Each channel includes its
server's `guild_id` (string) and `guild_name`. The dashboard refreshes this list
when the Discord selector opens. Only one voice connection is active at a time.

## Send controls

Every control request requires an `Idempotency-Key` header containing a UUID. Request
bodies use JSON. The supported operations are:

| Method and path                   | JSON body                                                                         |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `POST /api/queue`                 | `{"source_url": "https://www.youtube.com/watch?v=Pqp9fDRp1lw"}`                   |
| `POST /api/queue/batch`           | `{"source_urls": ["https://www.youtube.com/watch?v=Pqp9fDRp1lw"]}`               |
| `DELETE /api/queue/{entry_id}`    | No body                                                                           |
| `POST /api/queue/{entry_id}/move` | `{"before_entry_id": null, "expected_queue_revision": 3}`                         |
| `POST /api/queue/clear`           | `{"expected_queue_revision": 3}`                                                  |
| `POST /api/queue/undo`            | `{"undo_id": "REMOVAL_UUID"}`                                                      |
| `POST /api/player/play`           | `{"expected_playback_id": null}` when idle; the current playback ID when resuming |
| `POST /api/player/pause`          | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `POST /api/player/skip`           | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `POST /api/player/stop`           | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `PUT /api/player/volume`          | `{"volume": 0.5}`; range 0 to 1                                                   |
| `PUT /api/player/seek`            | `{"position_seconds": 75, "expected_playback_id": "CURRENT_PLAYBACK_UUID"}`      |
| `PUT /api/voice/channel`          | `{"channel_id": "CHANNEL_ID"}`                                                    |
| `DELETE /api/voice/channel`       | No body                                                                           |

Moving before `null` puts the entry last. Remove, move and clear affect upcoming
entries. Use skip or stop for the current track. Both add endpoints accept video
URLs. Batch adds accept 1 to 100 URLs, append
the entire block in order, and commit it with one queue revision. Repeated URLs
create distinct entries. Invalid input rejects the whole batch. `entry_id` is null
for batch responses; the snapshot contains the added entries. Set
`skip_duplicates: true` to omit videos already playing, queued or repeated within
the batch. Matching uses YouTube video IDs across URL variants and runs when the
batch is inserted. The default is false. Replies include `added_count` and
`skipped_count`, including when a request is replayed.

To clear one person's upcoming entries, include their contributor UUID as
`contributor_id` in `POST /api/queue/clear`. The filter matches IDs, not names.
Omitting it or sending null clears everyone's upcoming entries. Both operations
commit once, check `expected_queue_revision`, and preserve the current track and
playback history.

Removal replies include `removed_count`, `undo_id` and `undo_expires_at` when
entries were removed. The acting user has 12 seconds to send the undo ID to
`POST /api/queue/undo` with a new idempotency key. Undo restores the original entry
IDs, metadata and authors near their surviving neighbors. If those neighbors are
gone or reversed, entries are appended in their original order. Playback, history
and subsequent edits stay intact. After clearing the entire queue, later additions
remain ahead of restored entries.

Undo is single-use and returns `restored_count`. Expired, consumed or another
user's undo IDs return HTTP 410 with `code: "undo_unavailable"`. A retry with the
same idempotency key replays the original outcome without restoring twice.

Add, remove, clear and undo replies include `actor` (the acting user's profile)
and `entries` (the affected tracks). These are stored with the operation result,
so retries retain the original names and metadata even after profile edits or
removal from the queue. Older receipts can have no actor or entries. New tracks
may still have pending metadata; subsequent state updates provide their titles.

Seeking moves the shared Discord audio to an absolute position in seconds.
The position must be at least zero and less than the current track's duration.
Only playing and paused tracks can seek; a paused track stays paused.

Mutation responses contain `request_id`, `code`, `entry_id` (for additions),
`replayed` and a fresh `snapshot`. Successful operations use HTTP 200 and
`code: "ok"`.

Playback issues have a stable `id`, the affected `entry` when available, and a
public `reason`: `source_unavailable`, `stream_interrupted`, `voice_unavailable`
or `backend_halted`. Use the issue ID to suppress repeated notifications across
snapshots and reconnects. Internal exception text and stream URLs are not exposed.

## Find music

`GET /api/catalog/search?q=TITLE_OR_ARTIST&offset=0` searches YouTube Music songs
by default (`source=youtube_music`). Set `source=youtube` to search videos instead.
It returns an `entries` array of
up to 10 results, a `snapshot_id` and a `next_offset`. Pass the snapshot ID and
offset to load the next page from the same result set;
null marks the end. Offsets are multiples of 10 from 0 to 90, allowing up to
100 results per search. The query must contain 1 to 200 characters.

Each result includes an `index`, `source_url`, public track metadata and an
`unavailable` reason when the entry cannot be added. The backend keeps up to 32
queries in memory. After five minutes, another lookup returns the saved results
and starts a shared background refresh. A failed refresh keeps the saved snapshot.
`refreshing` and `refresh_error` describe that work.

While `refreshing` is true, poll with the displayed `snapshot_id`.
If `latest_snapshot_id` differs, fetch that version separately and offer it to the
user without changing the displayed list. Subsequent pages must use the accepted
version. Versions expire after 30 minutes and can be evicted sooner by the cache
limit; an expired or mismatched version returns 410. Never append an unversioned
page to an existing list. Clients can deduplicate search results by video ID.
Unknown metadata fields are null. Search results contain no stream URLs.

Open a playlist with `POST /api/youtube/playlists`, a JSON `source_url` and a UUID
`Idempotency-Key`. The response is HTTP 202 with a preview whose `id` matches that
key. Repeating the same request from the same account returns the existing preview.
Only its owner can read or cancel it. Video links with a
`list` parameter also work here; `POST /api/queue` still adds only their video.

Poll `GET /api/youtube/playlists/{id}` for progress. A preview moves from `loading`
to `ready`, `failed` or `cancelled`. Its `entries` arrive in source order. Each has
a unique index even when a video appears more than once. Known unavailable
entries remain visible with a reason. `error` explains a failure; a `ready`
preview can contain usable entries alongside a partial-load error.

Playlist contents are shared across previews, but access to each preview remains
with its owner. Opening a known playlist returns its cached contents immediately
and refreshes them at most once per minute. A new `snapshot_id` indicates changed
contents. Keep those changes separate until the user accepts them, then reconcile
selection by track identity. Duplicate occurrences remain separate; ambiguous
matches and new tracks stay unselected. A failed or partial refresh leaves the
previous complete snapshot available, with a `refresh_error`.

Previews contain at most 100 entries. `truncated: true` means more exist beyond
that limit. Submit selected, available URLs to `POST /api/queue/batch` in preview
order. Loading a preview never changes the queue.

`DELETE /api/youtube/playlists/{id}` cancels that user's loading preview. The
extractor stops when no other preview needs it. It needs no request key; repeated
cancellation has the same effect. Completed previews remain readable for ten
minutes; reopening a completed preview renews that period. Expired or unknown
IDs return 410, and previews disappear on backend restart.

The catalog retains at most 32 playlists and 1,000 individual tracks in memory.
Adding a known link uses cached metadata immediately. Stale details refresh
through the queue's metadata worker after five minutes and only enrich matching
entries that still exist. Temporary audio URLs use the separate playback resolver.

Discovery allows two extractions at once and up to eight active or waiting jobs.
Search and metadata extraction have a 30-second deadline; playlists have 60
seconds. Slot waiting counts toward an outer deadline of 35 or 65 seconds.
Capacity errors return 429 with `Retry-After: 5`; extraction errors return 502.
Playback uses a separate resolver and does not wait for a discovery slot.

## Handle conflicts and retries

Keep the newest snapshot by its global `revision`. Ignore snapshots older than
the one already displayed, whether they arrive over HTTP or SSE.

Send the displayed `queue_revision` with reorder and clear requests. Changes to
volume or pause state do not invalidate it. If another operation changed the
upcoming queue, the API returns HTTP 409 with `code: "queue_conflict"` and the
current snapshot. Refresh the view before submitting a new action.

Playback controls target the displayed `playback_id`. A mismatch returns 409
with `code: "playback_conflict"`. This prevents two skips for one track from
also skipping the next track. Pausing and resuming retain the same playback ID;
seeking, starting another track or retrying extraction creates a new one.

If a response is lost, retry with the same request ID and payload. The outcome
is retained across restarts, and `replayed` is true on a retry. Its snapshot is
fresh, so a replay does not roll the display back. Reusing an ID with different
content or from another account returns 409 with `code: "idempotency_conflict"`.
Renaming your profile does not change the identity of a retry.

A request left unfinished by a process exit returns 409 with
`code: "interrupted"`. Inspect its snapshot before deciding to send a new request
ID. The backend does not automatically repeat an uncertain Discord action.

Unknown queue entries return 404 (`entry_not_found`). Invalid FSM actions return
409 (`invalid_action`), and invalid input returns 422. Voice failures return 503
(`voice_unavailable`). A halted or unavailable backend returns 503
(`backend_unavailable`); disable controls until state can be read again.

## Receive live updates

Connect an `EventSource` to `/api/events` and listen for `state` events. Each
event contains a complete snapshot and uses its global revision as the SSE ID.
Every connection starts with the current snapshot, including reconnects with
`Last-Event-ID`. There is no event history to replay.

Slow clients may skip intermediate snapshots. The latest one contains the full
state. Closing a browser has no effect on playback.

Access is checked before each snapshot and every two seconds while idle. Logout,
expiry or whitelist removal sends an `auth` event with an error `code` and closes
the stream within five seconds. Close the client `EventSource` on this event so
it does not keep reconnecting with an invalid session.

While playing, estimate progress from `position_seconds` plus elapsed time since
`position_updated_at`. Hold that position while paused and use the new anchor
after a seek or state change. Use `PUT /api/player/seek` to change the position.

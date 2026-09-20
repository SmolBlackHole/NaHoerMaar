# Control API

Parent: [Development guide](development.md)

The local API runs at `http://127.0.0.1:8000`. Its interactive reference is at
`/docs`, with an OpenAPI schema at `/openapi.json`.

## Read state

`GET /api/state` returns the current track, upcoming entries, playback and voice
states, channel ID, volume, playback ID, progress anchors and any playback issue.
Titles, artists, uploader links, duration and thumbnails arrive asynchronously;
unavailable fields remain null. Discord channel IDs are strings; queue and
playback IDs are UUIDs.

Each entry includes `added_by`, either null or the browser profile's `id` (UUID),
`name` (1 to 32 characters) and `avatar` (four hexadecimal characters). Send that
object alongside `source_url` when adding a track. The profile is saved with the
entry and its playback history. Requeuing records the person adding it again.
Additions without a profile are attributed to `Anonymous`; older entries can
still have null attribution.
Browser profiles are self-chosen names, not authenticated Discord identities.

`recently_played` contains up to 100 starts, newest first. Each item has its own
`id`, a timezone-aware `played_at` timestamp and an `entry` with track metadata.
Skipped tracks remain in history; unplayed removals do not enter it. Pause/resume
and seeking or automatic stream retries do not add another start. Requeue a history item
through `POST /api/queue` with its source URL.

`GET /api/channels` lists voice channels with `can_connect` and `can_speak` flags.
Each channel includes its server's `guild_id` (string) and `guild_name`.

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
URLs. Batch adds accept 1 to 100 URLs and an optional `added_by` profile, append
the entire block in order, and commit it with one queue revision. Repeated URLs
create distinct entries. Invalid input rejects the whole batch. `entry_id` is null
for batch responses; the snapshot contains the added entries.

To clear one person's upcoming entries, include their browser profile UUID as
`contributor_id` in `POST /api/queue/clear`. The filter matches IDs, not names.
Omitting it or sending null clears everyone's upcoming entries. Both operations
commit once, check `expected_queue_revision`, and preserve the current track and
playback history.

Seeking moves the shared Discord audio to an absolute position in seconds.
The position must be at least zero and less than the current track's duration.
Only playing and paused tracks can seek; a paused track stays paused.

Mutation responses contain `request_id`, `code`, `entry_id` (for additions),
`replayed` and a fresh `snapshot`. Successful operations use HTTP 200 and
`code: "ok"`.

## Find music

`GET /api/catalog/search?q=TITLE_OR_ARTIST&offset=0` searches YouTube Music songs
by default (`source=youtube_music`). Set `source=youtube` to search videos instead.
It returns an `entries` array of
up to 10 results and a `next_offset`. Pass that offset to load the next page;
null marks the end. Offsets are multiples of 10 from 0 to 90, allowing up to
100 results per search. The query must contain 1 to 200 characters.

Each result includes an `index`, `source_url`, public track metadata and an
`unavailable` reason when the entry cannot be added. The backend caches up to 100
results per source and query for five minutes, so pages keep the same order during
that period. The cache holds at most 32 queries; identical concurrent searches
share one lookup. Failed searches are not cached. Clients should deduplicate by
video ID because results can change after cache expiry.
Unknown metadata fields are null. Search results contain no stream URLs.

Open a playlist with `POST /api/youtube/playlists`, a JSON `source_url` and a UUID
`Idempotency-Key`. The response is HTTP 202 with a preview whose `id` matches that
key. Repeating the same request returns the existing preview. Video links with a
`list` parameter also work here; `POST /api/queue` still adds only their video.

Poll `GET /api/youtube/playlists/{id}` for progress. A preview moves from `loading`
to `ready`, `failed` or `cancelled`. Its `entries` arrive in source order. Each has
a unique index even when a video appears more than once. Known unavailable
entries remain visible with a reason. `error` explains a failure; a `ready`
preview can contain usable entries alongside a partial-load error.

Previews contain at most 100 entries. `truncated: true` means more exist beyond
that limit. Submit selected, available URLs to `POST /api/queue/batch` in preview
order. Loading a preview never changes the queue.

`DELETE /api/youtube/playlists/{id}` cancels a loading preview and waits for its
extractor to stop. It needs no request key; repeated cancellation has the same
effect. Completed previews remain readable for ten minutes. Expired or unknown
IDs return 410, and previews disappear on backend restart.

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
content returns 409 with `code: "idempotency_conflict"`.

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

While playing, estimate progress from `position_seconds` plus elapsed time since
`position_updated_at`. Hold that position while paused and use the new anchor
after a seek or state change. Use `PUT /api/player/seek` to change the position.

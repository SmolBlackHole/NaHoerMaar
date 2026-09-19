# Control API

Parent: [Development guide](development.md)

The local API runs at `http://127.0.0.1:8000`. Its interactive reference is at
`/docs`, with an OpenAPI schema at `/openapi.json`.

## Read state

`GET /api/state` returns the current track, upcoming entries, playback and voice
states, channel ID, volume, playback ID, progress anchors and any playback issue.
Metadata fields may be null until metadata enrichment is implemented. Discord
channel IDs are strings; queue and playback IDs are UUIDs.

`GET /api/channels` lists voice channels with `can_connect` and `can_speak` flags.

## Send controls

Every mutation requires an `Idempotency-Key` header containing a UUID. Request
bodies use JSON. The supported operations are:

| Method and path                   | JSON body                                                                         |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `POST /api/queue`                 | `{"source_url": "https://www.youtube.com/watch?v=Pqp9fDRp1lw"}`                   |
| `DELETE /api/queue/{entry_id}`    | No body                                                                           |
| `POST /api/queue/{entry_id}/move` | `{"before_entry_id": null, "expected_queue_revision": 3}`                         |
| `POST /api/queue/clear`           | `{"expected_queue_revision": 3}`                                                  |
| `POST /api/player/play`           | `{"expected_playback_id": null}` when idle; the current playback ID when resuming |
| `POST /api/player/pause`          | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `POST /api/player/skip`           | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `POST /api/player/stop`           | `{"expected_playback_id": "CURRENT_PLAYBACK_UUID"}`                               |
| `PUT /api/player/volume`          | `{"volume": 0.5}`; range 0 to 1                                                   |
| `PUT /api/voice/channel`          | `{"channel_id": "CHANNEL_ID"}`                                                    |
| `DELETE /api/voice/channel`       | No body                                                                           |

Moving before `null` puts the entry last. Remove, move and clear affect upcoming
entries. Use skip or stop for the current track. The API accepts individual
YouTube video links; search and playlist import are not available yet.

Mutation responses contain `request_id`, `code`, `entry_id` (for additions),
`replayed` and a fresh `snapshot`. Successful operations use HTTP 200 and
`code: "ok"`.

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
starting another track or retrying extraction creates a new one.

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
after the next state change. This is display progress, not a seek interface.

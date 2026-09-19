# Architecture

Parent: [Project README](../README.md)

## Queue and player state

The Python core manages one current track and an ordered queue. Each entry has
its own UUID, so adding the same video twice produces independently editable
entries. Snapshots and entries are immutable.

One synchronous `Player` owns the state. Queue operations address entry IDs;
removing or moving the current track requires a playback action instead.
Clearing the queue leaves the current track alone. Stopping puts it first in
the queue and waits for a new start from the beginning.

Playback follows a deterministic finite-state machine. The
[transition table](../backend/src/nahormaar_backend/fsm.py) defines every allowed
state/event pair:

| State     | `play`    | `ready`   | `pause`  | `skip` | `stop` | `fail`  | `finished` |
| --------- | --------- | --------- | -------- | ------ | ------ | ------- | ---------- |
| `idle`    | next      | -         | -        | `idle` | `idle` | -       | -          |
| `loading` | -         | `playing` | -        | next   | `idle` | `error` | -          |
| `playing` | -         | -         | `paused` | next   | `idle` | `error` | next       |
| `paused`  | `playing` | -         | -        | next   | `idle` | `error` | next       |
| `error`   | `loading` | -         | -        | next   | `idle` | -       | -          |

`next` takes the next queued entry into `loading`, or becomes `idle` when the
queue is empty. `play` from `error` retries the same entry. A dash means the
event raises `InvalidTransitionError` without changing state. `finished` also
applies while paused because a pause can race with the final audio frame.

SQLAlchemy maps queue entries and player state to SQLite. Each persistent change
commits in one Session transaction before the Player publishes its new snapshot.
Failed writes and failed commits leave the previous snapshot intact.

Creating a Player runs the FSM's `recover` event: an interrupted track returns
to the front of the queue, playback becomes `idle`, and the separate voice
state becomes `disconnected`. Recovery is saved before the Player is usable;
another restart does not duplicate the entry. Unknown schemas and invalid
stored states raise `StorageError` without replacing the database.

## Playback and voice

The [PlaybackController](../backend/src/nahormaar_backend/playback.py) serializes
controls and callbacks. One worker thread owns the synchronous Player and its
database; Discord runs on the asyncio event loop. Each playback attempt has its
own ID. Late extraction results and completion callbacks are ignored once that
attempt has been stopped, skipped or replaced.

The [YouTube resolver](../backend/src/nahormaar_backend/youtube.py) runs `yt-dlp`
in a cancellable child process with a 30-second deadline. It resolves one finite,
public video immediately before playback and selects the best available audio.
Stream URLs, codec information and HTTP headers stay in memory.

For Opus sources, FFmpeg copies the encoded audio into an Ogg stream. At 100%
volume, compatible 20-ms packets reach Discord unchanged. Lower volume requires
decoding, scaling and Opus encoding with the music profile and a 512-kbit/s target.
Other codecs, packet durations or container gain also require conversion. Output
uses 48-kHz stereo with 20-ms frames.

Volume changes take effect within the running stream. The decoder follows the
original packets even during passthrough, so lowering the volume needs no new
extraction, connection or playback attempt. Returning to 100% restores the original
packets when the source supports passthrough.

Transient extraction or stream failures get one fresh resolution and restart
from the beginning. A failed track is then skipped, with its entry ID and error
available through `PlaybackStatus.last_issue`. A database failure stops audio and
blocks further controls until restart. The last committed snapshot remains intact;
`last_issue.fatal` reports that playback has halted. Other operational failures
persist `error` when the database is available. An unexpected Discord client exit
ends the owning runtime and triggers cleanup.

Voice has its own FSM: `disconnected` -> `connecting` -> `connected`. A failed join,
disconnect or channel change returns an interrupted track to the front of the
queue. Reconnecting requires a manual playback start. Channel selection, volume
and attempt IDs are runtime values.

Stop, disconnect and shutdown cancel extraction and reap the owned child processes.
FFmpeg cleanup completes before another audio source starts. The controller owns
accepted operations until they finish, even if their awaiting caller is cancelled.

## API and simultaneous changes

FastAPI owns one runtime through its lifespan. HTTP controls and playback
callbacks share the controller's lock. The API reads complete committed
snapshots and never writes to the database or Discord directly.

SQLite schema version 2 adds revisions and request receipts. Version 1 migrates
in a transaction, preserving entries and order. `revision` orders visible state
changes, including runtime-only changes such as volume. `queue_revision` changes
only when upcoming entries or their order change. Startup advances the global
revision because the voice connection and other runtime values reset.

Reorder and clear compare the client's queue revision under the controller lock.
Playback controls compare the expected playback attempt ID. A late skip for a
finished track cannot consume its successor.

Each mutation reserves its request ID before it runs. Queue edits commit their
outcome in the same transaction as the queue and revisions. Retrying an ID with
the same command returns its stored outcome and a fresh snapshot; reusing it for
another command fails. Receipts have no automatic expiry.

Discord effects cannot share a SQLite transaction. If the process exits before
their outcome is saved, recovery marks the request as interrupted. It does not
execute the request again. The client reads the current state before deciding
whether to submit a new request ID.

SSE subscribers register and receive their first snapshot under the same lock
as publication. Each subscriber buffers at most one snapshot, replacing an older
pending update when needed. Reconnect always starts with the current state.
The server closes event streams before draining HTTP requests during shutdown.
Failed database writes close streams and make API requests return 503 until
restart.

Playback progress uses position and timestamp anchors updated on playback
transitions. Displaying elapsed time needs no per-second database writes.

See the [API contract](api.md) for requests, responses and conflict handling.

## Dashboard

The Nuxt dashboard will provide login, voice channel selection, a queue and
playback controls through the API. All controls will live in the dashboard;
no Discord chat or slash commands are planned.

See the [roadmap](../ROADMAP.md) for upcoming work.

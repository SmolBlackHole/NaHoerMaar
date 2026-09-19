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
The `seek` event preserves `playing` or `paused` and leaves the queue unchanged;
it is invalid in other states.

SQLAlchemy maps queue entries and player state to SQLite. Each persistent change
commits in one Session transaction before the Player publishes its new snapshot.
Failed writes and failed commits leave the previous snapshot intact.

The last 100 started tracks are stored with timestamps and independent history
IDs. Recording a start and entering `playing` share one transaction. Skips keep
the history entry; removing an unplayed track creates none. Resuming or retrying
a stream automatically, or seeking within it, does not count twice.

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

One background task resolves missing metadata for upcoming entries. It processes
one entry at a time, outside the command lock. Results update existing IDs only,
so removing a track during extraction cannot bring it back. Playback resolution
also saves public metadata before the track starts.

For Opus sources, FFmpeg copies the encoded audio into an Ogg stream. At 100%
volume, compatible 20-ms packets reach Discord unchanged. Lower volume requires
decoding, scaling and Opus encoding with the music profile and a 512-kbit/s target.
Other codecs, packet durations or container gain also require conversion. Output
uses 48-kHz stereo with 20-ms frames.

Volume changes take effect within the running stream. The decoder follows the
original packets even during passthrough, so lowering the volume needs no new
extraction, connection or playback attempt. Returning to 100% restores the original
packets when the source supports passthrough.

Seeking restarts FFmpeg at the requested offset using the resolved stream URL.
It creates a new playback attempt, so callbacks and controls for the old stream
cannot affect the new one. Volume and pause state stay unchanged. Opus packets
before the target are discarded without re-encoding the remaining stream.

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

SQLite schema version 3 stores artist and channel metadata and playback history.
Versions 1 and 2 migrate in a transaction, preserving entries, order and existing
request receipts. `revision` orders visible state
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

The Nuxt dashboard controls playback, volume, voice channels and the shared queue.
Its server forwards HTTP requests and SSE to FastAPI on localhost. The proxy
keeps the API's local-host and same-origin restrictions.

The browser accepts snapshots by revision and disables mutations while
disconnected. Reorder and clear use the displayed queue revision; playback
actions use the displayed playback ID. A lost response leaves the request ID
and payload available for a safe retry. Closing the dashboard only closes its
event stream; playback continues.

Thumbnails come from track metadata or the YouTube video ID. Cover and Video fill
the player area, with a dark, blurred overlay behind the track details;
the settings button reveals the native YouTube controls and the full video frame.
The embed first loads while the Video view is visible and connected, initially muted.
Switching to Queue, hiding the browser tab or losing the connection pauses the preview
and keeps the embed. Returning aligns it with the bot's current position and play/pause
state. Track changes in the background load a new embed only when the player becomes
visible again. Selecting Cover or leaving the player page destroys the embed.
The native YouTube controls affect only the browser preview. It follows bot play/pause
changes, but does not synchronize viewers or compensate for Discord audio latency.

At the end of the video, the preview returns to Cover. YouTube's `rel=0` setting
limits recommendations to the same channel; it does not disable them. End cards
during playback can still appear.

Recently played groups matching video IDs and shows their playback count within
the stored 100 starts. Each group uses its latest entry and timestamp; the raw
history remains available for activity statistics.

Browser profiles store a name and a Pixabot avatar locally. They do not authenticate
API access. Discord login and the whitelist remain planned. The Overview uses
the retained playback history, not lifetime listening statistics.

See the [roadmap](../ROADMAP.md) for upcoming work.

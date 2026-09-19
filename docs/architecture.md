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
and attempt IDs are runtime values. The database schema is unchanged.

Stop, disconnect and shutdown cancel extraction and reap the owned child processes.
FFmpeg cleanup completes before another audio source starts. The controller owns
accepted operations until they finish, even if their awaiting caller is cancelled.

## Planned integrations

The Nuxt dashboard will provide login, voice channel selection, a queue and
playback controls through a FastAPI API. All controls will live in the dashboard;
no Discord chat or slash commands are planned.

See the [roadmap](../ROADMAP.md) for upcoming work.

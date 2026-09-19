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

| State     | `play`    | `ready`   | `pause`  | `skip` | `stop` | `fail`  |
| --------- | --------- | --------- | -------- | ------ | ------ | ------- |
| `idle`    | next      | -         | -        | `idle` | `idle` | -       |
| `loading` | -         | `playing` | -        | next   | `idle` | `error` |
| `playing` | -         | -         | `paused` | next   | `idle` | `error` |
| `paused`  | `playing` | -         | -        | next   | `idle` | `error` |
| `error`   | `loading` | -         | -        | next   | `idle` | -       |

`next` takes the next queued entry into `loading`, or becomes `idle` when the
queue is empty. `play` from `error` retries the same entry. A dash means the
event raises `InvalidTransitionError` without changing state. The `ready` and
`fail` events report playback outcomes; audio integration is still planned.

SQLAlchemy maps queue entries and player state to SQLite. Each change commits
in one Session transaction before the Player publishes its new snapshot.
Failed writes and failed commits leave the previous snapshot intact.

Creating a Player runs the FSM's `recover` event: an interrupted track returns
to the front of the queue, playback becomes `idle`, and the separate voice
state becomes `disconnected`. Recovery is saved before the Player is usable;
another restart does not duplicate the entry. Unknown schemas and invalid
stored states raise `StorageError` without replacing the database.

## Planned integrations

The Nuxt dashboard will provide login, voice channel selection, a queue and
playback controls through a FastAPI API. All controls will live in the dashboard;
no Discord chat or slash commands are planned.

The Python backend will use `yt-dlp` to resolve YouTube audio, FFmpeg to process
it and `discord.py` to play it in Discord voice channels.

See the [roadmap](../ROADMAP.md) for upcoming work.

# Playback

Parent: [Engine documentation](README.md)

Playback turns committed Session intent into Discord audio. The domain FSM
decides state and typed effects; the controller executes those effects against
providers and the Discord output adapter, then returns correlated results to the
Session. This page owns attempts, audio transitions and restart behavior.

## Table of contents

- [Playback](#playback)
  - [Table of contents](#table-of-contents)
  - [FSM and effects](#fsm-and-effects)
  - [Attempts and late results](#attempts-and-late-results)
  - [Audio path and crossfade](#audio-path-and-crossfade)
  - [Checkpoints and restart](#checkpoints-and-restart)
  - [Voice connection](#voice-connection)
  - [Shutdown order](#shutdown-order)

## FSM and effects

`player/fsm.py` is a pure state machine. Given the committed Session
snapshot and an input, it selects the next playback state and declares effects
such as resolve, start, pause, seek, prepare or stop. It does not access the
database, create tasks or call Discord.

`player/playback.py` executes those effects after the Session transaction
commits. Provider, preload and voice work happen outside the inbox. Results come
back as Session inputs, where the FSM decides whether they still belong to the
current state. The Session remains the only owner that replaces committed queue
and playback state.

## Attempts and late results

Each active output has an attempt ID. Connection, join, preparation and
transition work have their own correlation IDs. Seek, skip, stop, reorder or a
new connection can replace them. A delayed resolver response, audio completion
or Discord callback is accepted only when its identity still matches the state
that requested it.

The logical play ID is separate from an attempt. Seek, source retry, reconnect
and process restoration can create technical attempts while preserving one
confirmed play and its history count. Playback controls target the attempt the
caller displayed so a delayed pause or seek cannot affect its successor.

## Audio path and crossfade

`integrations/discord.py` adapts the player audio and voice protocols to
Discord. It owns FFmpeg processes, bounded source buffers, mixing and Opus
output. Provider audio URLs and headers exist only in active or prepared source
buffers; they are never durable track metadata.

At unity volume and outside an overlap, compatible Opus packets pass through
without decoding and encoding again. Volume changes and crossfade use the mixer.
Only consumed frames advance playback position and fade time. The next queue
occurrence may be prepared ahead of its transition; preparation is invalidated
when the owning attempt or entry changes. At most two source buffers are held
during an overlap.

Crossfade overlaps natural track transitions for up to the configured duration.
For a short outgoing track, the FSM limits the overlap to half of that track's
duration. A manual Skip remains an immediate control. Cancellation settles the
FFmpeg processes and buffers before their ownership is released. Playback timing
and buffer diagnostics are explained in
[Testing and acceptance](../testing.md#playback-diagnostics).

## Checkpoints and restart

The durable checkpoint stores the current entry and track, measured position,
playing or paused intent, attribution and logical play ID. Session settings keep
the selected channel, bot volume and crossfade duration. Checkpoints refresh
every five seconds and during a clean shutdown.

On startup, the engine restores the same listening-session identity, rejoins its
saved channel and resolves a fresh source for the retained track. A playing track
continues near its last checkpoint; an explicitly paused track stays paused.
History is not counted again. A restart during crossfade restores the incoming
track rather than replaying the outgoing tail.

The database schema and transaction ownership are documented in
[Database](database.md). File-level backup and restore are separate operational
procedures in [Back up and restore NaHörMaar](../recovery.md).

## Voice connection

An unexpected disconnect keeps the checkpoint and the saved channel. The
coordinator reconnects immediately, then retries after 1, 2, 5 and 10 seconds.
Each transition publishes a `VoiceConnectionChanged` event with the channel,
phase, attempt number and sanitized error type. The phases distinguish a pending
connection from a scheduled retry and an exhausted retry sequence.

After reconnecting, the coordinator checks whether the retained attempt still
has an audio output. If it does not, it resolves a fresh source and resumes from
the committed checkpoint. An idle connected bot may rejoin without starting the
queue. Explicit Leave clears the saved channel so startup stays disconnected.

If the saved channel no longer exists or cannot be entered, the track and
position remain available. After the bounded retries are exhausted, another
Join command, including `/pspsps`, starts a new attempt. Channel discovery and
Discord installation are covered by [Set up Discord](../discord-setup.md).

## Shutdown order

The application registers each runtime resource for cleanup before its startup
begins. Shutdown then closes playback, the Discord gateway, listening state and
the player in reverse order before releasing the catalog and database. A second
close is a no-op.

If startup fails, the same cleanup runs before the error leaves the composition
root. The FastAPI lifespan also calls close when startup raises, so a failed
migration, gateway login or playback start cannot leave an acquired resource
behind.

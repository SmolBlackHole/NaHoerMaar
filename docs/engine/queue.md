# Queue and history

Parent: [Engine documentation](README.md)

The queue owns ordered requests for tracks. History owns confirmed plays. They
refer to the same catalog tracks, but they represent different events and have
different lifetimes. This page explains those identities, queue mutations and
the point at which a request becomes listening history.

## Table of contents

- [Queue and history](#queue-and-history)
  - [Table of contents](#table-of-contents)
  - [Track, queue entry and play](#track-queue-entry-and-play)
  - [Queue mutations](#queue-mutations)
  - [Revisions, receipts and retries](#revisions-receipts-and-retries)
  - [Confirmed playback history](#confirmed-playback-history)
  - [Radio and the queue](#radio-and-the-queue)

## Track, queue entry and play

A track is the persistent catalog identity for a piece of music. A queue entry
is one request for that track. It has its own ID, position, origin and snapshot
of the person who added it. Adding the same track twice creates two independent
entries that can be moved or removed separately.

A playback record is one confirmed play. It is created after audio output starts,
not when somebody queues a track or when the engine begins resolving a source.
Seek, retry and restart retain the same logical play ID. Starting the track again
creates another play.

Contributor snapshots preserve the name and avatar shown for an earlier request
or play even if the account profile changes later. The internal track ID remains
the relationship to current metadata.

## Queue mutations

`engine/domain/queue.py` owns pure edits: add, remove, reorder, clear and restore.
`engine/session.py` applies them inside the serialized Session transaction. A
mutation never waits for provider lookup or Discord output while holding that
transaction.

Removing one entry affects only that occurrence. Bulk removal may target the
current contributor, a selected contributor or all upcoming entries. It leaves
the current play alone. A successful removal can issue an Undo token with a
12-second deadline; restoring uses the stored removal evidence rather than
reconstructing entries from the current queue.

Stop and clearing the queue are different operations. Stop puts the current
entry back at the front and resets playback. Clearing removes upcoming entries
while the current track continues. Their public commands are documented in the
[Engine API](../engine-api.md#state-and-mutations).

## Revisions, receipts and retries

The queue has its own revision inside the broader Session revision. Reorder and
bulk-clear commands include the revision the caller displayed, so an older view
cannot silently overwrite newer queue work.

Every mutation carries an idempotency key. The Session stores a receipt with the
actor, command fingerprint and outcome in the same transaction as the state
change. Repeating the same accepted operation returns its durable result and the
current state. Reusing its key for another actor or command conflicts.

The commit happens before an event is published or an audio effect starts. A
failed transaction exposes neither success nor a partly changed queue.

## Confirmed playback history

The checkpoint may name a current entry before its first audio frame arrives.
History begins only when output confirms the play. That distinction keeps failed
resolves and failed starts out of play counts.

When a play ends, its record receives a completion, skip, stop or failure reason.
An interrupted process can restore the current track and logical play without
counting it a second time. [Playback](playback.md) owns attempts, checkpoints and
the technical confirmation path.

## Radio and the queue

Manual requests and Radio entries use the same queue. Their origin distinguishes
how they arrived; it does not create another ordering system. Manual requests
count towards Radio's target and play before later refills. Radio reacts to
committed queue changes and submits an ordinary queue mutation when it has more
tracks. The complete refill policy lives in [Radio](radio.md).

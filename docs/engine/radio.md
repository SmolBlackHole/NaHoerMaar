# Radio

Parent: [Engine documentation](README.md)

Radio is an automatic queue-filling strategy. It uses a track or playlist as a
seed, asks the catalog's recommendation provider for related music and adds
ordinary queue entries as space opens. It does not own playback, maintain a
second queue or replace manual requests.

## Table of contents

- [Radio](#radio)
  - [Table of contents](#table-of-contents)
  - [Strategies](#strategies)
  - [Starting from a seed](#starting-from-a-seed)
  - [Refill loop](#refill-loop)
  - [Stale work and failures](#stale-work-and-failures)
  - [Pause, stop and restart](#pause-stop-and-restart)

## Strategies

The Session always has one queue-filling strategy. Manual mode adds nothing on
its own. Radio mode carries the seed, initiator, generation and unused
recommendation candidates. The strategies live in
`engine/domain/radio.py`; neither one plays audio.

Radio aims for three upcoming tracks. Existing manual entries count towards that
target, so Radio fills gaps instead of moving ahead of requests from listeners.
Tracks that are current, recent, already queued or explicitly excluded are
filtered before a candidate is added.

## Starting from a seed

A seed is a media reference. Starting from a track asks for recommendations
related to that track. Starting from a playlist retains the playlist reference.
The catalog passes that seed to its configured recommendation capability;
the current composition uses YouTube Music. The provider translates its result
into the same domain findings used by ordinary discovery.

This keeps source knowledge at the provider boundary. Radio decides when another
candidate is needed; the provider decides how related candidates are found. The
catalog resolves accepted findings to persistent tracks before they enter the
queue. See [Catalog and metadata](catalog.md).

## Refill loop

Radio observes committed Session changes through the post-commit event bus. When
the queue falls below its target, it requests more candidates outside the Session
inbox. The completed result re-enters the inbox and becomes one queue commit.
Listeners therefore see the same attribution, revisions and events as they do
for other queue changes.

Playing one Radio entry reduces the upcoming count. The next committed state
triggers another refill when needed. This is the whole playback relationship:
Radio maintains queue supply, while the normal queue and playback controllers
decide order and audio.

## Stale work and failures

Recommendation work carries the active Radio generation and request ID. Stopping
Radio, changing its seed or starting a newer request invalidates older results.
A late provider response cannot add tracks to the replacement strategy.

Candidates are consumed once and exclusions prevent loops through the same
tracks. If recommendation work fails, Radio retains its strategy and reports the
failure. A retry starts work for the same active generation. Provider work never
holds the Session transaction open.

## Pause, stop and restart

Pausing playback suspends new refills. An unexpected voice disconnect suspends
playback but retains Radio. Ending Radio stops future additions and leaves tracks
it already queued in place. Explicit Stop, Leave or a full queue clear ends the
active Radio strategy as part of the same committed Session change.

Radio state is durable. After a process restart, it restores the seed, initiator,
generation, exclusions and unused candidates, then fills any open places without
duplicating queued or recent tracks. The one-time `engine_0001` upgrade caveat
is documented in [Database](database.md#schema-ownership), and the listener-facing controls are in
[Listening together](../listening.md#let-radio-find-the-next-few-tracks).

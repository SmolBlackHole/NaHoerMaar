# Engine documentation

Parent: [Documentation index](../README.md)

The engine owns the shared listening session behind NaHörMaar. Its parts answer
different questions: the catalog identifies music, the Library saves reactions
and personal playlists, the queue orders requests, Radio decides when to find
more, playback turns committed intent into audio, Lyrics enriches a known track,
and the database keeps durable state. This index routes to the owner of each
part instead of repeating their rules in one architecture page.

## Table of contents

- [Engine documentation](#engine-documentation)
  - [Table of contents](#table-of-contents)
  - [Choose a topic](#choose-a-topic)
  - [Code map](#code-map)
  - [Reading order](#reading-order)

## Choose a topic

| Question | Owner |
| --- | --- |
| Links, searches and providers become tracks? | [Catalog](catalog.md) |
| Lyrics for a known track? | [Lyrics](lyrics.md) |
| Reactions and personal playlists? | [Library](library.md) |
| Queue entries and confirmed history? | [Queue](queue.md) |
| Radio refill and manual priority? | [Radio](radio.md) |
| FSM, audio, Discord and restart recovery? | [Playback](playback.md) |
| Storage, commits and migrations? | [Database](database.md) |

The [architecture overview](../architecture.md) shows how these owners connect.
The [Engine API](../engine-api.md) owns request, response and event shapes. These
pages describe the implementation boundaries behind that contract.

## Code map

| Path under `backend/src/nahoermaar/` | Guide |
| --- | --- |
| `bootstrap.py` and `messaging.py` | [Architecture](../architecture.md#command-and-event-flow) |
| `catalog/domain.py` | [Catalog](catalog.md) |
| `catalog/repository.py` and `catalog/service.py` | [Catalog](catalog.md) |
| `catalog/providers.py` and `integrations/youtube.py` | [Catalog](catalog.md) |
| `lyrics/` and `integrations/lrclib.py` | [Lyrics](lyrics.md) |
| `library/` | [Library](library.md) |
| `player/domain.py` and `player/fsm.py` | [Queue](queue.md) and [Radio](radio.md) |
| `player/session.py` and `player/events.py` | [Architecture](../architecture.md#command-and-event-flow) |
| `player/playback.py` | [Playback](playback.md) |
| `integrations/discord.py` and `integrations/audio.py` | [Playback](playback.md) |
| `database/core.py`, `database/uow.py` and `database/schema.py` | [Database](database.md) |
| `database/migrations/` | [Database](database.md) |
| `users/` | [Discord setup](../discord-setup.md) |
| `listening/` and `statistics/` | [Architecture](../architecture.md) |
| `api/` | [Engine API](../engine-api.md) |

## Reading order

To follow one request through the engine, read:

1. [Catalog and metadata](catalog.md), where a source becomes a persistent track.
2. [Library](library.md), where a user saves reactions and ordered playlists.
3. [Queue and history](queue.md), where one request becomes a queue occurrence.
4. [Playback](playback.md), where committed intent becomes Discord audio.
5. [Database](database.md), where the state and operation evidence are stored.

Read [Radio](radio.md) after the queue: Radio is a queue-filling strategy, not a
second player.

The [architecture overview](../architecture.md#command-and-event-flow) owns the
Session command path and post-commit event flow. It also points to the production
composition root instead of duplicating those engine-wide rules here.

# Engine documentation

Parent: [Documentation index](../README.md)

The engine owns the shared listening session behind NaHörMaar. Its parts answer
different questions: the catalog identifies music, the queue orders requests,
Radio decides when to find more, playback turns committed intent into audio,
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
| Queue entries and confirmed history? | [Queue](queue.md) |
| Radio refill and manual priority? | [Radio](radio.md) |
| FSM, audio, Discord and restart recovery? | [Playback](playback.md) |
| Storage, commits and migrations? | [Database](database.md) |

The [architecture overview](../architecture.md) shows how these owners connect.
The [Engine API](../engine-api.md) owns request, response and event shapes. These
pages describe the implementation boundaries behind that contract.

## Code map

| Path under `backend/src/nahormaar_backend/` | Guide |
| --- | --- |
| `engine/session.py` | [Architecture](../architecture.md#command-and-event-flow) |
| `engine/events.py` | [Architecture](../architecture.md#command-and-event-flow) |
| `engine/runtime.py` | [Architecture](../architecture.md#command-and-event-flow) |
| `engine/catalog.py` | [Catalog](catalog.md) |
| `engine/providers.py` | [Catalog](catalog.md) |
| `engine/youtube.py` | [Catalog](catalog.md) |
| `engine/metadata.py` | [Catalog](catalog.md) |
| `engine/domain/queue.py` | [Queue](queue.md) |
| Queue work in `engine/session.py` | [Queue](queue.md) |
| `engine/domain/radio.py` | [Radio](radio.md) |
| Radio work in `engine/session.py` | [Radio](radio.md) |
| `engine/domain/playback.py` | [Playback](playback.md) |
| `engine/playback.py` | [Playback](playback.md) |
| `engine/discord.py` and audio integrations | [Playback](playback.md) |
| `engine/persistence.py` | [Database](database.md) |
| `engine/schema.py` and `engine/migrations/` | [Database](database.md) |
| `persistence/` | [Database](database.md) |
| `engine/api.py` and `engine/api_models.py` | [Engine API](../engine-api.md) |
| `engine/http_auth.py` | [Engine API](../engine-api.md) |
| `engine/bootstrap.py` | [Composition](../architecture.md#composition-and-lifetime) |
| `engine/gateway.py` and `engine/commands.py` | [Discord setup](../discord-setup.md) |

## Reading order

To follow one request through the engine, read:

1. [Catalog and metadata](catalog.md), where a source becomes a persistent track.
2. [Queue and history](queue.md), where one request becomes a queue occurrence.
3. [Playback](playback.md), where committed intent becomes Discord audio.
4. [Database](database.md), where the state and operation evidence are stored.

Read [Radio](radio.md) after the queue: Radio is a queue-filling strategy, not a
second player.

The [architecture overview](../architecture.md#command-and-event-flow) owns the
Session command path and post-commit event flow. It also points to the production
composition root instead of duplicating those engine-wide rules here.

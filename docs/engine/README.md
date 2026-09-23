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

| Question                                                                  | Owner                              |
| ------------------------------------------------------------------------- | ---------------------------------- |
| How do links, searches, providers and metadata become tracks?             | [Catalog and metadata](catalog.md) |
| What is a queue entry, and when does history begin?                       | [Queue and history](queue.md)      |
| How does Radio refill without taking over manual requests?                | [Radio](radio.md)                  |
| How do the FSM, effects, Discord audio and restart recovery fit together? | [Playback](playback.md)            |
| What is stored, when does it commit, and how do migrations work?          | [Database](database.md)            |

The [architecture overview](../architecture.md) shows how these owners connect.
The [Engine API](../engine-api.md) owns request, response and event shapes. These
pages describe the implementation boundaries behind that contract.

## Code map

| Paths under `backend/src/nahormaar_backend/`                                               | Documentation owner                                                                                  |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `engine/session.py`, `engine/events.py`, `engine/runtime.py`                               | [Architecture](../architecture.md#command-and-event-flow)                                            |
| `engine/catalog.py`, `engine/providers.py`, `engine/youtube.py`, `engine/metadata.py`      | [Catalog and metadata](catalog.md)                                                                   |
| `engine/domain/queue.py`, queue handling in `engine/session.py`                            | [Queue and history](queue.md)                                                                        |
| `engine/domain/radio.py`, Radio work in `engine/session.py`                                | [Radio](radio.md)                                                                                    |
| `engine/domain/playback.py`, `engine/playback.py`, `engine/discord.py`, audio integrations | [Playback](playback.md)                                                                              |
| `engine/persistence.py`, `engine/schema.py`, `engine/migrations/`, `persistence/`          | [Database](database.md)                                                                              |
| `engine/api.py`, `engine/api_models.py`, `engine/http_auth.py`                             | [Engine API](../engine-api.md)                                                                       |
| `engine/bootstrap.py`, `engine/gateway.py`, `engine/commands.py`                           | [Architecture](../architecture.md#composition-and-lifetime) and [Discord setup](../discord-setup.md) |

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

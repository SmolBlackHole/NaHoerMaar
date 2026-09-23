# NaHörMaar documentation

Parent: [Project README](../README.md)

The [project README](../README.md) explains what NaHörMaar is and why it exists.
This index routes from that introduction to listener guides, system design,
engine domains, operation and the public API. Each fact has one owning page;
other pages keep the context they need and link to that owner.

## Table of contents

- [NaHörMaar documentation](#nahörmaar-documentation)
  - [Table of contents](#table-of-contents)
  - [Choose a starting point](#choose-a-starting-point)
  - [Read it as a book](#read-it-as-a-book)
  - [Documentation ownership](#documentation-ownership)
    - [Product and operation](#product-and-operation)
    - [System and contracts](#system-and-contracts)
    - [Project documentation](#project-documentation)

## Choose a starting point

| I want to... | Start here | Continue with |
| --- | --- | --- |
| Listen and request music | [Listening](listening.md) | [Radio](engine/radio.md) |
| Set up Discord and access | [Discord setup](discord-setup.md) | [Development](development.md) |
| Run NaHörMaar locally | [Development](development.md) | [Hosting](hosting.md) |
| Understand the system | [Architecture](architecture.md) | [Engine](engine/) or [frontend](frontend.md) |
| Change search or metadata | [Catalog](engine/catalog.md) | [Engine API][api-discovery] |
| Change queue order or history | [Queue](engine/queue.md) | [Database](engine/database.md) |
| Change Radio | [Radio](engine/radio.md) | [Catalog](engine/catalog.md) |
| Change Discord audio or restart | [Playback](engine/playback.md) | [Testing][playback-diagnostics] |
| Change tables or migrations | [Database](engine/database.md) | [Recovery](recovery.md) |
| Change the dashboard | [Frontend](frontend.md) | [Engine API](engine-api.md) |
| Change an endpoint | [Engine API](engine-api.md) | [Architecture](architecture.md#command-and-event-flow) |
| Verify behavior | [Testing](testing.md) | [Roadmap](../ROADMAP.md) |

[api-discovery]: engine-api.md#discovery-and-stable-selections
[playback-diagnostics]: testing.md#playback-diagnostics

## Read it as a book

For a guided introduction:

1. [Project README](../README.md): purpose, origin and current boundary.
2. [Listening together](listening.md): what the group can do.
3. [Architecture](architecture.md): how one request crosses the system.
4. [Engine documentation](engine/): catalog, queue, Radio, playback and database.
5. [Frontend architecture](frontend.md): repositories, stores and local workflows.
6. Choose [Development](development.md), [Engine API](engine-api.md) or
   [Testing](testing.md) for the work in front of you.

The reference pages are also direct entry points. You do not need to read the
whole sequence to change one provider or develop one migration.

## Documentation ownership

### Product and operation

| Page | Owns |
| --- | --- |
| [Listening together](listening.md) | Listener-visible controls and behavior |
| [Set up Discord](discord-setup.md) | Developer Portal, installation, OAuth redirect and access roles |
| [Development](development.md) | Local setup, commands and service startup |
| [Hosting](hosting.md) | Production Compose deployment, HTTPS and host capacity |
| [Backup and restore](recovery.md) | PostgreSQL dumps, verification, retention and restore procedure |
| [Testing and acceptance](testing.md) | Automated checks, diagnostics and live listening evidence |

### System and contracts

| Page | Owns |
| --- | --- |
| [Architecture](architecture.md) | End-to-end flow and responsibility boundaries |
| [Engine documentation](engine/) | Engine navigation and domain ownership |
| [Frontend architecture](frontend.md) | Generated types, repositories, stores and composables |
| [Engine API](engine-api.md) | HTTP, SSE, request and response shapes |

### Project documentation

| Page | Owns |
| --- | --- |
| [Writing guide](writing-and-maintaining-docs.md) | Documentation ownership, structure and review rules |
| [Roadmap](../ROADMAP.md) | Work that is not implemented |
| [Contributing](../CONTRIBUTING.md) | Change workflow and contribution expectations |
| [Security](../SECURITY.md) | Private vulnerability reports |

Read the writing guide before adding or moving a page. It keeps the tree from
drifting back into one large file.

Behavior descriptions refer to the implemented version unless they say
otherwise. The [project status](../README.md#where-it-stands) owns the current
product boundary; unfinished work belongs in the [roadmap](../ROADMAP.md).

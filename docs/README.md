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

| I want to...                                      | Start here                                | Continue when needed                                                              |
| ------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------- |
| Listen with friends and request music             | [Listening together](listening.md)        | [Radio](engine/radio.md) for its exact refill behavior                            |
| Set up Discord sign-in, the bot and the whitelist | [Set up Discord](discord-setup.md)        | [Development](development.md) to run the services                                 |
| Run NaHörMaar locally                             | [Development](development.md)             | [Hosting](hosting.md) for an always-on machine                                    |
| Understand the whole system                       | [Architecture](architecture.md)           | [Engine documentation](engine/) or [Frontend architecture](frontend.md)           |
| Work on search, links, playlists or metadata      | [Catalog and metadata](engine/catalog.md) | [Engine API](engine-api.md#discovery-and-stable-selections) for the wire contract |
| Work on queue ordering or history                 | [Queue and history](engine/queue.md)      | [Database](engine/database.md) for persistence                                    |
| Work on Radio                                     | [Radio](engine/radio.md)                  | [Catalog and metadata](engine/catalog.md) for provider recommendations            |
| Work on Discord audio or restart behavior         | [Playback](engine/playback.md)            | [Testing](testing.md#playback-diagnostics) for diagnostics                        |
| Change tables or migrations                       | [Database](engine/database.md)            | [Backup and restore](recovery.md) for operations                                  |
| Change the dashboard                              | [Frontend architecture](frontend.md)      | [Engine API](engine-api.md) for its backend contract                              |
| Call or change an endpoint                        | [Engine API](engine-api.md)               | [Architecture](architecture.md#command-and-event-flow) for the path behind it     |
| Check behavior or investigate playback            | [Testing and acceptance](testing.md)      | [Roadmap](../ROADMAP.md) for unfinished work                                      |

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

| Page                                 | Owns                                                         |
| ------------------------------------ | ------------------------------------------------------------ |
| [Listening together](listening.md)   | Listener-visible controls and behavior                       |
| [Set up Discord](discord-setup.md)   | Developer Portal, installation, OAuth redirect and whitelist |
| [Development](development.md)        | Local setup, commands and service startup                    |
| [Hosting](hosting.md)                | Host constraints and capacity questions                      |
| [Backup and restore](recovery.md)    | SQLite copies, verification, retention and restore procedure |
| [Testing and acceptance](testing.md) | Automated checks, diagnostics and live listening evidence    |

### System and contracts

| Page                                 | Owns                                                  |
| ------------------------------------ | ----------------------------------------------------- |
| [Architecture](architecture.md)      | End-to-end flow and responsibility boundaries         |
| [Engine documentation](engine/)      | Engine navigation and domain ownership                |
| [Frontend architecture](frontend.md) | Generated types, repositories, stores and composables |
| [Engine API](engine-api.md)          | HTTP, SSE, request and response shapes                |

### Project documentation

| Page                                                                     | Owns                                                |
| ------------------------------------------------------------------------ | --------------------------------------------------- |
| [Writing and maintaining documentation](writing-and-maintaining-docs.md) | Documentation ownership, structure and review rules |
| [Roadmap](../ROADMAP.md)                                                 | Work that is not implemented                        |
| [Contributing](../CONTRIBUTING.md)                                       | Change workflow and contribution expectations       |
| [Security](../SECURITY.md)                                               | Private vulnerability reports                       |

Read the writing guide before adding or moving a page. It keeps the tree from
drifting back into one large file.

Behavior descriptions refer to the implemented version unless they say
otherwise. The [project status](../README.md#where-it-stands) owns the current
product boundary; unfinished work belongs in the [roadmap](../ROADMAP.md).

# NaHörMaar documentation

Parent: [Project README](../README.md)

NaHörMaar has one shared listening session. The guides below separate what a
listener does, how a contributor runs the project, and what the backend promises
to clients. Start with the [project README](../README.md) for the short version.

## Choose a starting point

| I want to...                                 | Read                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------ |
| Listen with friends and use the queue        | [Listening together](listening.md)                                       |
| Set up the bot and dashboard locally         | [Development guide](development.md)                                      |
| Understand the engine and module boundaries  | [Architecture](architecture.md)                                          |
| Call or change the HTTP and event API        | [Engine API](engine-api.md)                                              |
| Run checks or review live listening evidence | [Testing and acceptance](testing.md)                                     |
| See what exists and what comes next          | [Roadmap](../ROADMAP.md)                                                 |
| Contribute or report a security issue        | [Contributing](../CONTRIBUTING.md) and [security policy](../SECURITY.md) |

## Where information belongs

Each topic has one home. [Listening together](listening.md) owns visible player
behavior. [Architecture](architecture.md) owns state, data flow and code
responsibilities. [Engine API](engine-api.md) owns the HTTP/SSE contract.
[Development](development.md) owns setup and operational configuration;
[testing](testing.md) owns validation and its remaining gaps. The
[roadmap](../ROADMAP.md) tracks unfinished work. Other pages link to these
owners instead of restating their rules.

This is a guide to the current code, not a promise of independent queues for
each Discord server. That change and other planned features are explicitly
marked in the roadmap.

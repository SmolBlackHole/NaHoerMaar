# NaHörMaar documentation

Parent: [Project README](../README.md)

The [project README](../README.md) introduces NaHörMaar and explains why it
exists. These pages answer the next questions separately: how to listen, how a
request travels through the system, how to run it, and what its API promises.

## Choose a starting point

| I want to... | Start here | Then, if needed |
| ------------ | ---------- | --------------- |
| Join friends and request music | [Listening together](listening.md) | [Discord setup](discord-setup.md) for the owner who sets up access |
| Set up the Discord application and whitelist | [Discord setup](discord-setup.md) | [Development](development.md) to start the services |
| Run the bot and dashboard | [Development](development.md) | [Hosting considerations](hosting.md) for an always-on machine, [Backup and restore](recovery.md) for its data |
| Understand who owns queue, Radio and playback | [Architecture](architecture.md) | [Engine API](engine-api.md) for the public contract |
| Change the frontend | [Frontend ownership](architecture.md#frontend-ownership) | [API type generation](development.md#update-the-frontend-api-contract) |
| Call or change an endpoint | [Engine API](engine-api.md) | [Architecture](architecture.md#command-and-event-flow) for the path behind it |
| Review what is unfinished | [Roadmap](../ROADMAP.md) | [Live acceptance](testing.md#live-acceptance) for unproven behavior |

For a guided read, go from the [project README](../README.md) to
[listening together](listening.md), then [follow a request through the architecture](architecture.md#command-and-event-flow).
After that, use the development, API or testing page that matches your work.

## Where information belongs

The pages have different jobs. A listener should not have to read an endpoint
reference to find out what Stop does; an API client should not have to infer a
response shape from a UI description.

### Using NaHörMaar

[Listening together](listening.md) owns the visible behavior: sign-in, finding
music, queue controls, playback, browser audio and Radio. The
[project README](../README.md) owns the short introduction, origin and current
product boundary.

### Building and running it

[Architecture](architecture.md) follows a request through the frontend, engine,
providers, storage and Discord output. It explains state and module ownership.
[Development](development.md) owns local setup, starting services, restarts and
updating generated API types. [Discord setup](discord-setup.md) owns the portal,
installation, OAuth redirect and whitelist. [Hosting considerations](hosting.md)
covers what a home server or VPS would need; it is not a tested deployment recipe.
[Backup and restore](recovery.md) owns database copies, retention, scheduling and
the offline restore procedure. [Testing](testing.md) owns the checks, diagnostic
procedure and live listening evidence.

### Contracts and unfinished work

[Engine API](engine-api.md) is the HTTP and SSE reference. Its request, response
and event shapes are the public contract, independent of how the dashboard
chooses to display them. [Roadmap](../ROADMAP.md) holds features and fixes that
are not complete. [Contributing](../CONTRIBUTING.md) explains how to change these
pages without maintaining a second version of the same rule, and the
[security policy](../SECURITY.md) covers private vulnerability reports.

Behavior descriptions refer to the current implementation unless marked as
future work. In particular, separate queues per Discord server are planned,
not available today.

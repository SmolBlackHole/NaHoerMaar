# Contributing to NaHörMaar

Parent: [Project README](README.md)

NaHörMaar is in active development. Focused fixes are welcome. Discuss changes
to the shared-session model, public API, authentication or Discord playback
behavior before building a large replacement around them.

## Work locally

Use the [development guide](docs/development.md) for setup, configuration and
running the two services. The [architecture](docs/architecture.md) connects the
system boundaries; the [engine index](docs/engine/) routes to each backend
domain, and the [engine API](docs/engine-api.md) owns public request and event
shapes. Do not manually edit the generated TypeScript API types. The
[frontend architecture](docs/frontend.md#change-the-api-contract) shows how to
regenerate them after a backend contract change.

Keep changes focused. Add or update tests for behavior changes, then run the
relevant checks from [testing and acceptance](docs/testing.md). The full local
gate is `python scripts/dev.py check`. Tests use isolated state, but optional
audio recordings consume extra CPU. Live Discord listening or a backend restart
must be coordinated with people using the bot.

## Keep documentation current

The [documentation guide](docs/writing-and-maintaining-docs.md) names the owner
of each topic. Update that page when behavior changes, then link to it from other
pages instead of maintaining a second explanation. Mark future behavior in the
[roadmap](ROADMAP.md); don't describe it as available today. Validate local links
with `python scripts/repository_checks.py`.

When proposing a change, describe its behavior, any API or access effect, the
checks that passed, and what remains unverified. Report vulnerabilities through
the private process in [SECURITY.md](SECURITY.md).

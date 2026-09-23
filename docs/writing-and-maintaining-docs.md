# Writing and maintaining NaHörMaar documentation

Parent: [Documentation index](README.md)

Each fact in the NaHörMaar documentation has one owner. A useful page answers a
concrete question or removes an ambiguity; it should not create another version
of a rule that must be kept vaguely in sync.

## Table of contents

- [Writing and maintaining NaHörMaar documentation](#writing-and-maintaining-nahörmaar-documentation)
	- [Table of contents](#table-of-contents)
	- [Choose the owner](#choose-the-owner)
	- [Define a fact once](#define-a-fact-once)
	- [Structure and navigation](#structure-and-navigation)
	- [Review changes](#review-changes)

## Choose the owner

| Content                                            | Owner                     |
| -------------------------------------------------- | ------------------------- |
| Project purpose, name, first setup link and status | Root `README.md`          |
| Listener-visible behavior                          | `docs/listening.md`       |
| System flow and responsibility map                 | `docs/architecture.md`    |
| Frontend boundaries and generated types            | `docs/frontend.md`        |
| Catalog, metadata and discovery caches             | `docs/engine/catalog.md`  |
| Queue occurrences, revisions and history           | `docs/engine/queue.md`    |
| Radio strategy and refill behavior                 | `docs/engine/radio.md`    |
| Playback FSM, audio output and restart behavior    | `docs/engine/playback.md` |
| SQLAlchemy, transactions, schema and migrations    | `docs/engine/database.md` |
| HTTP, SSE, request and response shapes             | `docs/engine-api.md`      |
| Local setup, commands and service startup          | `docs/development.md`     |
| Discord Developer Portal and whitelist setup       | `docs/discord-setup.md`   |
| Hosting constraints                                | `docs/hosting.md`         |
| Backup and restore operations                      | `docs/recovery.md`        |
| Automated checks, diagnostics and live evidence    | `docs/testing.md`         |
| Work that is not implemented                       | `ROADMAP.md`              |

If a paragraph fits two rows, split the facts by authority and link between the
owners. The API reference may name what a Radio endpoint does; the Radio page
owns why and when the strategy refills.

## Define a fact once

Explain a behavior or contract completely on its owning page. A consuming page
keeps only the consequence its reader needs and links to the owner at that point.
Names and short reminders may repeat when they keep a page readable, but defaults,
limits, lifecycle rules, recovery behavior and transaction guarantees should not.

The root README is an introduction, not a compressed copy of the technical
documentation. The architecture page connects boundaries; it does not absorb the
complete catalog, queue, Radio, playback, database and frontend references.

## Structure and navigation

Every meaningful branch with several pages has a `README.md` that explains the
category and routes to its leaves. The branch index does not copy their behavior.
Do not create an empty branch for possible future pages.

Every page starts with one `Parent:` link after its title. The parent is the
nearest useful index: root documentation links to the documentation index, while
Engine leaves link to the Engine index. Put a short introduction before the
first section so a reader knows what the page owns.

Use the literal heading `## Table of contents` and the Markdown All in One list
shape used throughout this repository. A non-index page needs that list when it
has four or more main sections or nested sections. A flat page with at most three
main sections may omit it, as may a branch `README.md` when the list would add no
navigation value.

Use contextual links where another owner answers the next question. A final pile
of unrelated links does not replace navigation in the paragraph that needs it.
When adding or moving a page, update the nearest branch index and the root index
when readers need a new entry point.

Describe implemented behavior by default. Put unfinished work in the roadmap and
say plainly when a future boundary must be mentioned. Do not let a desired design
read as a shipped feature.

## Review changes

After changing documentation:

1. Read the owning page again as a new contributor or listener.
2. Search for the same rule elsewhere and replace duplicate explanations with a
   useful link.
3. Check that names, paths, commands, limits and public behavior still match the
   source and tests.
4. Run `python scripts/repository_checks.py` to validate UTF-8, final newlines,
   local Markdown links, reachability and the Git diff.

Documentation-only changes do not need the backend and frontend test suites
unless they alter generated artifacts or uncover a contract mismatch that needs
code changes.

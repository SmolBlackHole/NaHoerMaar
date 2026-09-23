# Frontend architecture

Parent: [Documentation index](README.md)

The Nuxt dashboard presents one shared Session without owning playback. Typed
repositories cross the HTTP boundary, stores own durable client state, and local
composables own temporary workflows such as search and playlist selection. This
page explains those boundaries and the generated API contract.

## Table of contents

- [Frontend architecture](#frontend-architecture)
  - [Table of contents](#table-of-contents)
  - [Generated contract and repositories](#generated-contract-and-repositories)
  - [Shared state and live updates](#shared-state-and-live-updates)
  - [Local discovery workflow](#local-discovery-workflow)
  - [Accounts and appearance](#accounts-and-appearance)
  - [Components and notifications](#components-and-notifications)
  - [Change the API contract](#change-the-api-contract)

## Generated contract and repositories

`frontend/shared/api.generated.ts` is generated from the backend OpenAPI models.
`shared/engine.ts` names the wire types used by the player and projects track
references into display data. Generated code is a contract artifact; do not edit
or reformat it manually.

`app/repositories/` owns typed endpoint calls:

- Session handles queue, playback, connection, Radio and decoded SSE events.
- Catalog handles searches, links and pinned discovery snapshots.
- Account handles login state, profile and appearance.

One shared HTTP transport supplies timeouts, cancellation, JSON and empty
responses, normalized errors and CSRF. A Nuxt plugin provides one repository set
per app through Vue injection. Repositories read authentication hooks at request
time; they do not import Pinia stores or UI components.

## Shared state and live updates

`stores/player.ts` owns the current Session snapshot, playback clock anchor,
pending operations and SSE subscription lifetime. The default layout starts and
disposes that lifetime with the signed-in account. HTTP responses and SSE changes
pass through the same revision checks and operation deduplication, so their arrival
order cannot apply a command twice.

The frontend treats the backend snapshot as authoritative. It may show pending
intent for feedback, but it does not manufacture a second queue or advance the
shared playback clock on its own. A stale response from an earlier account
generation is discarded.

## Local discovery workflow

`useCatalog` owns local results, pagination, refresh polling and cancellation.
`useDiscovery` owns the surrounding search, playlist import and selection flow.
The Vue panel owns focus, scrolling and responsive presentation. Closing it
cancels pending work.

Displayed result versions stay pinned until the listener accepts an update.
Playlist selections refer to persistent track IDs; playlist positions remain
separate occurrences. Queue duplicate checks also use track IDs. Only
`stores/radioPreview.ts` shares a proposed Radio seed between entry points; the
active Radio belongs to the Session snapshot.

The backend behavior behind this UI lives in
[Catalog and metadata](engine/catalog.md) and [Radio](engine/radio.md).

## Accounts and appearance

`stores/profile.ts` owns identity, session expiry and cross-tab refresh.
`stores/settings.ts` owns appearance, debounce and retry. A remote refresh cannot
overwrite unsaved local appearance changes. Changing accounts invalidates pending
requests, clears private view state and binds the new account's settings.

The Account repository uses the same transport as Catalog and Session. Do not add
another fetch wrapper to a profile or settings store.

## Components and notifications

Components receive display data and call store or workflow actions. They do not
construct queue entries, choose API routes or own request retries. Track
presentation components can render metadata without pretending that the track is
already queued.

`usePlayerNotifications` maps semantic actions and outcomes to toasts. Retries
retain the original request body and idempotency key. Loading indicators and
messages use actions such as `queue.reordered` or `playback.seek`, not URL string
matching.

The optional browser video follows the logical play ID across seeks and
reconnects. Its local volume is separate from Discord bot volume and starts
muted. The browser is never the source of shared Discord audio.

## Change the API contract

Public response models live in
`backend/src/nahormaar_backend/engine/api_models.py`; request models and routes
live in `engine/api.py`. After changing them, run:

```powershell
npm run api:generate --workspace frontend
```

Generation exports OpenAPI in an isolated Python process and runs the locally
installed `openapi-typescript`. It does not enter the application lifespan,
connect to Discord or open the player database. Commit the generated TypeScript
with the backend contract change. Verify reproducibility with:

```powershell
npm run api:check --workspace frontend
```

Frontend tests inject repositories into a fresh Vue app and real Pinia through
`test/repository-fixture.ts`. Their fetch and EventSource doubles do not access
the live bot, queue or account. The full frontend and repository checks are in
[Testing and acceptance](testing.md).

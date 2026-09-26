# Frontend architecture

Parent: [Documentation index](README.md)

The Nuxt dashboard presents the shared NaHörMaar session. Playback remains in
the Python backend. The frontend receives authoritative state through HTTP and
SSE, then turns that state into the player, queue, profiles, statistics and
administration views.

## Table of contents

- [Frontend architecture](#frontend-architecture)
  - [Table of contents](#table-of-contents)
  - [Code boundaries](#code-boundaries)
  - [Generated contract and repositories](#generated-contract-and-repositories)
  - [Shared state and SSE](#shared-state-and-sse)
  - [Page workflows](#page-workflows)
  - [Components and appearance](#components-and-appearance)
  - [Change the API contract](#change-the-api-contract)
  - [Test the frontend](#test-the-frontend)

## Code boundaries

The frontend core lives under `frontend/app/core/`:

- `api/` contains the generated OpenAPI schema, the HTTP transport and SSE
  decoding.
- `models/` names the data used by the application. Pages and components do not
  import generated schema types directly.
- `repositories/` owns endpoint paths, methods and request bodies.
- `stores/` owns state that survives a route change: the signed-in account, the
  current profile and the shared player session.
- `workflows/` owns temporary page state such as a search, log page, access list
  or statistics period.

`plugins/backend-core.ts` creates one core instance for the Nuxt application.
Pages and components consume that instance through `$backendCore`. They do not
choose API routes or construct transport requests.

## Generated contract and repositories

`frontend/app/core/api/schema.generated.ts` is generated from the OpenAPI schema
of the Python package `nahoermaar`. It is a transport artifact and must not be
edited or reformatted by hand.

Repositories are grouped by backend capability: account, access, catalog,
listening, logs, player and statistics. They all use the same transport, which
adds CSRF credentials, normalizes API errors and rejects responses that belong
to an earlier account generation.

The browser calls Nuxt under `/api/`. Nitro forwards the request to the backend,
so the browser stays on one origin in local development and in the Docker stack.

## Shared state and SSE

The session store restores the current account and owns its authentication
generation. Signing out or switching accounts invalidates in-flight private
requests.

The profile store loads the current profile once for that account. Appearance
and profile forms update the same state instead of maintaining a second account
copy.

The player store owns the current player snapshot, voice channels, pending
commands and the SSE subscription. A connect or reconnect begins with a complete
snapshot. Later change events and HTTP replies pass through the same revision and
operation-ID checks, which prevents one command from being applied twice.

The backend remains authoritative for queue order. Dragging an entry sends the
observed queue revision and leaves the visible order unchanged until HTTP or SSE
returns the accepted state. Mutations are never retried automatically after an
uncertain network result. An explicit retry reuses the original operation ID.

## Page workflows

Discovery, recent playback, profiles, statistics, access and logs use local
workflow instances. Each workflow owns its loading state, error, cancellation
and current result. Leaving the page disposes that state.

Use a Pinia store only when several views need the same data or when the data has
a longer lifecycle than one page. A search result, selected statistics period or
log cursor does not belong in a global store.

## Components and appearance

Components render core models and call store or workflow actions. They do not
construct queue entries or infer successful playback before the backend confirms
it. Player notifications use the semantic action and outcome returned by the
backend.

`stores/settings.ts` keeps the editable appearance state because the theme must
remain available across the whole app. Persistence still goes through the core
profile store. Local browser video volume is separate from Discord bot volume.

## Change the API contract

Public request and response models live beside the routes in the Python backend.
After changing them, regenerate the TypeScript contract from the repository root:

```powershell
npm run api:generate --workspace frontend
```

The generator exports OpenAPI in an isolated Python process and runs the local
`openapi-typescript` installation. It does not connect to Discord, start audio or
open the live application database.

Commit the generated TypeScript with the backend contract change. Check that a
second generation produces no diff with:

```powershell
npm run api:check --workspace frontend
```

## Test the frontend

Core tests live under `frontend/test/core/`. Fetch and EventSource doubles cover
authentication changes, routes, queue revisions, SSE reconnects and operation
deduplication without touching the running bot.

Run the complete frontend gate with:

```powershell
npm run check --workspace frontend
```

The command checks the generated API contract, runs Vitest and TypeScript, then
builds the production Nuxt application.

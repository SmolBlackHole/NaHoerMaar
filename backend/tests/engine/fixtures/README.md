# Public contract baseline

These are synthetic examples of the existing HTTP/SSE contract, recorded for the
rewrite on 2026-09-21. No live server, account, database or provider was used.

Sources checked: `docs/api.md`, `api/schemas.py`, the public route declarations,
`api/mutations.py`, `api/boundary.py`, `api/events.py`, and the frontend's
`shared/player.ts`, `shared/catalog.ts` and `shared/radio.ts` contracts.
The fixtures do not import or serialize the old Player, Session or controllers.

`wire.json` contains complete JSON response bodies and named HTTP scenarios.
Each response's `body` names an entry in `bodies`. `setup` states the precondition;
the scenarios are independent, not a script to run against a live application.
Dates, IDs and credentials are fixed test values. Authenticated cases require a
fixture account/session and the common request headers. The signed-out case must
omit them. JSON bodies are omitted for GET/DELETE requests. Common response
headers also apply to errors and SSE. Framework-generated validation error prose
and provider-generated failure prose are not frozen as exact user-facing copy.

The baseline includes null/pending metadata, Unicode, separate queue/history/play
identities, measured pause position, actor/entry snapshots, a 12-second undo,
idempotent replay with a fresh snapshot, conflicts, stale discovery versions,
duplicate and unavailable playlist entries, radio state and channel permissions.
`reconnect.sse` records the current state on reconnect, regardless of the client's
old Last-Event-ID, followed by an authorization event that ends the stream.

Phase 1 checks fixture integrity, relationships and framing only. These checks
do **not** prove HTTP authorization, command execution, actual stream closure,
adapter cleanup, audio output or compatibility of a running new application.

The later approved plan permits a new API without legacy wire compatibility.
These fixtures remain historical behavior examples, not phase 5 response-shape
requirements. Direct engine API and SSE acceptance lives in `test_engine_api.py`;
the current contract is documented in `docs/engine-api.md` at the repository root.

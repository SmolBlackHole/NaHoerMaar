# Control API

Parent: [Development guide](development.md)

The backend now exposes the [native engine API](engine-api.md). That document is
the contract for authentication, discovery, queue and playback commands, conflict
handling and SSE. The old player routes and event shapes have been removed.

The local [API explorer](http://127.0.0.1:8000/docs) and
[OpenAPI schema](http://127.0.0.1:8000/openapi.json) describe the running service.
Protected calls need a session cookie; mutations also need the configured origin
and CSRF token. Queue, playback, connection and radio commands additionally use
an `Idempotency-Key` UUID.

The Nuxt proxy forwards this contract directly. The player client resolves track
references into display entries without sending that projection back to the API.
Public types are generated from OpenAPI; see
[contract generation](development.md#update-the-frontend-api-contract).
See [engine verification](engine-api.md#verification) for the checked boundaries
and the outstanding Discord listening acceptance.

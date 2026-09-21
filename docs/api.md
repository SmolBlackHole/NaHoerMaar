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

The existing frontend still uses the old contract and cannot control this backend
until its separate adaptation. See the [rewrite status](../refactoring.md) for
cutover evidence and the outstanding Discord listening acceptance.

# Architecture

Parent: [Project README](../README.md)

The backend runs one listening session through the engine and its native API.
The old business core, routes and data importer have been removed. The frontend
uses this API directly; the remaining Discord listening acceptance
is described under [engine verification](engine-api.md#verification).

## Ownership

`frontend/shared/api.generated.ts` is generated offline from the public API
models, including the account and appearance contract. `shared/engine.ts` names
wire types and projects track references into display data.

`app/repositories/` owns typed endpoint calls. Session handles queue, playback,
connection, radio and decoded SSE events; Catalog handles search, links and pinned
discovery snapshots; Account handles sessions, profile and appearance. A shared
HTTP transport supplies timeouts, cancellation, JSON/empty responses, errors and
CSRF. The Nuxt plugin provides one set per app through Vue injection. Auth hooks
read the profile store at request time; repositories never import stores or UI.
Responses from an earlier account generation are discarded.

`stores/player.ts` owns one session snapshot, the playback clock anchor, pending
operations and the SSE subscription lifetime. Its display snapshot is computed.
The default layout starts and disposes this lifetime with the signed-in account.
HTTP and SSE pass through the same revision checks and operation deduplication.
`usePlayerNotifications` translates the resulting activity into toasts using
semantic actions and request IDs; retry preserves the original payload and key.
Playback controls target an attempt, while video rendering keeps the logical
play ID across seeks and reconnects.

`useDiscovery` owns search/import/selection workflow; the Vue panel owns focus and
scroll. `useCatalog` owns local results, pagination, refresh polling and cancellation;
displayed versions stay pinned until the listener accepts an update. Closing the
panel cancels pending work. Queue duplicate checks use persistent track IDs;
playlist positions identify separate occurrences. `stores/radioPreview.ts` shares
only the proposed radio seed between entry points and reuses known references;
the active radio belongs to the player session.
Track presentation components accept metadata without manufacturing queue entries.

`stores/profile.ts` owns identity, session expiry and cross-tab refresh.
`stores/settings.ts` owns appearance, debounce and retry. A refresh cannot overwrite
unsaved local appearance changes. Account changes invalidate pending requests,
clear private view state and bind the new account's settings.

| Module | Responsibility |
| --- | --- |
| `engine/bootstrap.py` | Production composition: configuration, schema, Discord readiness, commands, presence and lifetime |
| `engine/runtime.py` | Own injected resources and compose metadata, catalog and Session |
| `engine/session.py` | Serialize mutations, own committed state and transactions, deliver post-commit events |
| `engine/domain/queue.py` | Pure queue editing, revision conflicts, attribution and 12-second Undo |
| `engine/domain/playback.py` | Pure FSM decisions: next state and typed effects |
| `engine/playback.py` | Execute effects, manage resolver/preload tasks and report correlated results |
| `engine/domain/radio.py` | Manual and radio queue-filling strategies |
| `engine/catalog.py` | Select providers and coordinate discovery, audio resolution and cache snapshots |
| `engine/providers.py`, `engine/youtube.py` | Provider protocol and YouTube/Music translation |
| `engine/metadata.py` | Merge observations into persistent track and artist identities |
| `engine/persistence.py` | Async SQLAlchemy repositories and explicit transaction boundaries |
| `engine/discord.py` | Voice and audio adapter; reuse FFmpeg, buffering, mixing and Opus mechanisms |
| `engine/gateway.py`, `engine/commands.py` | Discord events, presence and whitelist-protected `/pspsps` |
| `engine/api.py`, `engine/http_auth.py` | Native HTTP/SSE and account authorization |
| `application/auth.py`, `application/access.py` | OAuth login, session lifecycle and live whitelist |
| `domain/identity.py`, `domain/accounts.py`, `domain/preferences.py` | Shared account and contributor values |
| `persistence/accounts.py`, `persistence/models.py` | Account/login storage using short independent transactions |
| `integrations/` | Reused audio processing, process ownership, provider subprocesses, OAuth and daily bio |

The event loop is supplied by the application runtime. A Session has one
bounded inbox (128 pending messages); it waits for work rather than polling.
It is the only owner that replaces committed playback/queue state. HTTP, Discord
commands and technical callbacks all use this boundary.

## Command and event flow

```mermaid
flowchart LR
    HTTP[HTTP commands] --> Inbox[Session inbox]
    Discord[Discord commands and callbacks] --> Inbox
    Inbox --> Policy[Queue rules and playback FSM]
    Policy --> Commit[SQLAlchemy transaction]
    Commit --> State[Committed snapshot and receipt]
    State --> Bus[Post-commit event bus]
    State --> Effects[Playback effects]
    Effects --> IO[Provider and audio / voice]
    IO --> Inbox
    Bus --> SSE[Authenticated SSE]
    Bus --> Refill[Radio refill observer]
    Refill --> Inbox
```

A command checks its revision or attempt precondition inside the inbox. Queue,
checkpoint, history, receipt and revision updates commit before state is exposed.
A failed transaction cannot publish success. A repeated operation ID from the
same actor and command returns its durable result with current state; conflicting
reuse fails. Accepted work survives cancellation of its HTTP caller.

The FSM contains playback policy, without database access or background tasks.
Its effects execute after commit. Resolver, voice and audio results carry attempt,
connection or preparation IDs, so late results cannot modify their successors.
History begins only after confirmed media output, once per logical play.
Pause, seek, retry and restart retain that play identity.

The in-memory event bus is separate from the transaction and from SSE transport.
Normal subscribers have bounded FIFOs; overflow detaches a slow consumer.
The radio observer coalesces invalidations and recomputes against current state.
SSE starts with a complete snapshot, checks access during idle periods and before
delivery, and resynchronizes on reconnect. It is not a durable activity log.

## Catalog, metadata and radio

Providers translate source-specific results into domain findings and own source
recognition, search, playlists, recommendations and audio resolution. The catalog
chooses the provider; it does not know YouTube response shapes. Temporary audio
URLs and headers stay in memory and are never track metadata.

Tracks have persistent IDs and source identities. Artist entities use explicit
provider identities; display text alone does not invent an artist. Queue
occurrences and playback records reference tracks instead of copying their
metadata. Adding the same track twice creates two independently editable entries.
Contributor snapshots preserve attribution even after a profile changes.

Searches, playlists and track observations use bounded caches with shared refresh
work. Versioned discovery snapshots keep selections stable while fresh results
arrive. Queue additions use track IDs from the displayed version, not positions
in a possibly refreshed list.

Manual mode adds nothing automatically. Radio mode fills the queue towards three
upcoming entries using the seed provider's recommendations. Manual entries count
towards that target; current/recent/queued or excluded tracks are filtered.
Generation and request IDs reject stale results after stop or seed changes.
Provider work runs outside the inbox; results re-enter it for a single queue
commit. Paused playback is preserved. Radio is not restored automatically after
a process restart, but its queued entries remain.

## Audio and recovery

`DiscordOutput` implements the audio and voice protocols. The reused
`integrations/audio_sources.py` and `audio_mixer.py` own decoding, bounded buffers,
mixing and Opus output. Compatible Opus packets pass through unchanged at unity
volume outside overlaps. Volume changes and crossfade use the mixer; only consumed
audio frames advance position and fade time.

Preparation belongs to the current attempt and next queue occurrence. Seek,
reorder, stop or disconnect can invalidate it. At most two source buffers are
owned during a transition. Cancellation settles subprocesses and buffers before
their ownership is released.

Checkpoints record measured position, playing/paused intent and the chosen
channel. They refresh every five seconds and on clean shutdown. Startup restores
the same listening-session identity, rejoins its saved channel and resumes the
retained track. Explicit pause stays paused. Explicit Leave clears automatic
rejoin intent; an unexpected disconnect suspends without an immediate rejoin loop.
Joining again resumes through the same FSM, including `/pspsps`.

Shutdown closes SSE before HTTP drain, settles Session work, freezes output and
saves its checkpoint, then closes effects, catalog/provider work, storage, Auth
and the gateway. Partial startup also releases acquired resources. The normal
entry point reserves the API port before Discord login and runs one worker.

## Storage and access

The engine schema has a frozen Alembic chain under `engine/migrations/`.
Startup initializes an empty database and its single listening session atomically.
It rejects old or foreign databases without replacing them. The approved cutover
uses `data/engine.sqlite3`; the old database is retained separately and no import
runs. Account tables share the database but not the Session's playback transactions.

Discord OAuth uses `identify` and PKCE. Login attempts are browser-bound and
single-use. Seven-day session cookies are HTTP-only; only their hashes are stored.
Every protected request checks the current whitelist. Mutations also require
the configured origin and session-bound CSRF token. Account IDs and contributor
values come from authentication, never client-supplied attribution.

There is currently one shared queue and one active voice connection across the
bot's servers. Independent server sessions and Spotify matching are separate
work. See the [API contract](engine-api.md),
[development guide](development.md) and [roadmap](../ROADMAP.md).

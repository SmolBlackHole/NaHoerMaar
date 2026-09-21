# Architecture

Parent: [Project README](../README.md)

## Backend modules

- `domain/` defines immutable values, commands and FSM transitions without
  database, HTTP or Discord dependencies
- `application/` coordinates playback, identity and discovery. The Session
  serializes mutations; its worker owns the synchronous player and database
- `api/` owns FastAPI routes, request and response schemas, authentication checks
  and SSE transport
- `persistence/` owns SQLAlchemy mappings, repositories and Alembic migrations
- `integrations/` handles Discord voice and OAuth, YouTube extraction and child
  processes

`runtime.py` composes the Discord gateway, shared catalogs and SessionManager. The API
lifespan owns that runtime and the authentication service. Configuration stays
in `config.py`.

## Session ownership

`application/session.py` contains the single Session manager, shared state mirror,
request handling and status publication. These closely related roles stay in one
module. The Session keeps the existing public control operations as delegates;
HTTP and Discord commands enter the same awaited inbox. Its worker's `Player`
remains the authoritative state and transaction owner.

| Owner                        | Resources and responsibilities                                                                                                                        |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime                      | Discord gateway, shared catalog caches, recommendation previews and their extractors; concrete adapter and store construction                         |
| SessionManager               | Create, expose and close exactly one Session                                                                                                          |
| Session                      | One inbox receiver, receipts, revisions, committed-event subscriptions, latest-only SSE, metadata and checkpoint tasks, component and worker teardown |
| Queue                        | Editing, duplicate filtering and undo through the shared commit boundary                                                                              |
| PlaybackController           | Resolver tasks, audio attempts, recovery, crossfade preparation and audio effects                                                                     |
| RadioController (`radio.py`) | Active radio state, recommendation pool and refill tasks; preview service remains runtime-owned                                                       |
| DiscordVoice                 | Audio sources, voice connection and connection monitoring                                                                                             |
| DiscordGateway               | Client connection, command registration, presence and daily profile tasks                                                                             |

The Session borrows shared catalogs; closing it does not close those caches.
`catalog.py` resolves provider identity and chooses known metadata sources for
new queue entries. `metadata.py` owns the shared field-merge rules: only known
public metadata is copied, without replacing entry identity or attribution.
Queue and Radio receive source identity operations by injection.
The runtime's store factory creates SQLite storage on the same worker thread that
loads, uses and closes it. Checkpoint reconciliation lives in application code;
snapshot, revision, receipt and checkpoint changes still commit atomically.

Shutdown first rejects new Session work and closes SSE. It cancels checkpoint and
subscriber tasks, lets the active command finish and rejects pending inbox work.
It then cancels radio and metadata tasks, saves the current position, reaps
audio/resolution/preparation, and closes voice and storage. Runtime then closes
recommendation previews and extractors, the shared catalog, and finally the
Discord gateway. Cleanup attempts the remaining
owned resources even if one fails; partial startup also releases acquired resources.

The inbox introduced in [the backend refactor](../TODO.md) uses typed messages
instead of closure dispatch and a mutation lock. Resolver, metadata and recommendation work
runs outside the inbox; correlated results return through it. Audio-thread
callbacks cross onto the event loop without waiting for database or subscriber
work. The FSM owns interruption, resume and confirmed-start decisions.

The inbox admits up to 128 pending commands, then backpressures their callers.
Once admitted, a command survives cancellation of its caller. Sixteen additional
slots are reserved for nonblocking audio/connection results, in the same FIFO.
Exhausting that reserve logs an overload and halts playback explicitly; pending
callers receive errors. The receiver waits when idle, with no polling loop.

After persistence and revision publication succeed, the Session emits immutable
`QueueChanged`, `PlaybackChanged`, `RadioChanged` and confirmed `TrackStarted`
facts. Explicit subscribers
wake metadata enrichment, request radio refill and update crossfade preparation.
These internal invalidations coalesce to the latest pending change and recompute
current state. Ordinary subscribers keep a bounded FIFO (64 pending facts by
default); exceptions are logged independently, and overflow detaches that
consumer. Subscriptions support drain and close. This in-memory bus is separate
from SSE and does not promise crash-safe event delivery. `TrackStarted` follows
the history commit, once per logical play; it is not emitted for buffering,
crossfade reservation or a failed output start.

Radio retains continuation intent when a committed snapshot enters loading or
playing. The Session records that observation before publishing coalesced
invalidations, so an EOF cannot erase it before a delayed refill runs. After
handling a refill or provider result, Radio resumes queued work when the current
track has ended and voice is still connected, including when manual additions
already filled the buffer. Starting Radio while idle does not start playback;
stopping Radio clears the intent, and an explicitly paused track stays paused.

## Queue and player state

The Python core manages one current track and an ordered queue. Each entry has
its own UUID, so adding the same video twice produces independently editable
entries. Snapshots and entries are immutable.

One synchronous `Player` owns the state. Queue operations address entry IDs;
removing or moving the current track requires a playback action instead.
Clearing the queue leaves the current track alone. Stopping puts it first in
the queue and waits for a new start from the beginning.
Removal selection is shared by the state mutation, durable outcome and Undo.
Single-entry, contributor and whole-queue removal therefore act on the same
entry IDs. The worker commits the queue, receipt and Undo record together.

Playback follows the pure
[lifecycle FSM](../backend/src/nahormaar_backend/domain/fsm.py). Its decision
contains a new snapshot, a `PlaybackContext` and typed effects. The controller
commits the decision and its checkpoint before executing audio effects. The
context owns output-attempt identity, retry count, pause/resume intent, retained
position and whether this logical play has already been recorded.

| Event | Decision |
| --- | --- |
| Play | Start queued work, resume a paused track or reload a suspended track; active playback is unchanged |
| Pause / seek | Preserve the current entry; seek creates a new output attempt and preserves pause |
| Confirmed start | First media output changes loading to playing, or retains pause, and records history once |
| Natural completion | Advance once; ignore completion from a replaced attempt |
| Recoverable interruption | Resolve a fresh stream and retry once at the captured position |
| Failed output / exhausted retry | Report the affected track and advance once |
| Skip / stop | Skip advances; Stop requeues the current entry from the beginning without retry |
| Join | Resume retained playback or start queued work; preserve an explicit pause |
| Leave / connection loss | Stop output and retain current entry, position and pause; only explicit Leave clears automatic restart rejoin |
| Process restoration | Rejoin the saved channel; resume the retained track, or remain idle after Stop |

Pause also accepts a loading track. Seeking requires playing or paused state.
Invalid user transitions raise `InvalidTransitionError`; stale technical messages
leave the decision unchanged. There is no automatic reconnect loop after a lost
voice connection.

Crossfade reserves the incoming entry while the outgoing audio continues. History
is recorded only when the adapter confirms incoming media output. If activation
fails, conventional playback gets a distinct attempt ID; late callbacks cannot
complete that fallback or count it twice.

SQLAlchemy defines the tables and handles database access; Alembic applies schema
migrations at startup. Existing databases are adopted using their legacy version
marker. Migration and stored-data validation share a transaction, so invalid data
leaves the original database unchanged.

Each persistent player change
commits in one Session transaction before the Player publishes its new snapshot.
Failed writes and failed commits leave the previous snapshot intact.

The last 100 started tracks are stored with timestamps and independent history
IDs. Recording a confirmed start and its playback/checkpoint state share one
transaction, including a pause that races with the start. Skips keep
the history entry; removing an unplayed track creates none. Resuming or retrying
a stream automatically, or seeking within it, does not count twice.

The singleton `playback_checkpoint` stores the connected channel, current entry
ID, audio position, pause state, volume and `history_recorded` marker. Lifecycle
decisions commit that restart intent with the corresponding snapshot. Queue and
metadata edits reconcile the existing checkpoint without resetting the retained
track position. The Session refreshes the position every five seconds
and freezes the audio clock before saving on clean shutdown. These writes do not
increment public revisions or emit SSE updates.

Creating a Player with a checkpoint retains the current entry in `loading`.
Once the Discord gateway is ready, the controller rejoins the saved channel and
resolves a fresh stream at that offset. Paused playback starts silently and stays
paused. The checkpoint's `history_recorded` marker prevents another play count;
an earlier history entry for the same queue ID does not identify a new replay.
Migration `0011` infers this marker from history once for legacy checkpoints;
their original replay intent cannot be reconstructed reliably.
A shutdown during startup preserves a checkpoint that has not been restored yet.
During a crossfade the position belongs to the incoming track; its outgoing tail
is not restored. Radio replenishment does not restart automatically.

Without a checkpoint, the FSM's `recover` event returns an interrupted track to
the front of the queue, idle and disconnected. This also handles databases from
before checkpoint support. A failed channel restoration reports a playback issue
and preserves the queue for manual connection. Unknown schemas and invalid stored
states raise `StorageError` without replacing the database.

## Playback and voice

The [Session](../backend/src/nahormaar_backend/application/session.py) serializes
controls and callbacks. Its [PlaybackController](../backend/src/nahormaar_backend/application/playback.py)
coordinates audio effects. One worker thread owns the synchronous Player and its
database; Discord runs on the asyncio event loop. Each playback attempt has its
own ID. Late extraction results and completion callbacks are ignored once that
attempt has been stopped, skipped or replaced.

The Discord adapter discovers joined servers and their voice channels from
Discord's guild cache. No guild ID is configured. All servers share the same
queue, with one active voice connection. Switching servers disconnects the old
channel before joining the new one and resumes retained playback at its saved
position. Joining with no current track starts queued work. `/pspsps` and the
dashboard use this same operation; joining an already active channel does not
restart its track.
Late disconnect events from the old channel cannot close the new connection.

The [YouTube resolver](../backend/src/nahormaar_backend/integrations/youtube.py) runs `yt-dlp`
in a cancellable child process with a 30-second deadline. It resolves one finite,
public video immediately before playback and selects the best available audio.
Stream URLs, codec information and HTTP headers stay in memory.

The [metadata task](../backend/src/nahormaar_backend/application/metadata.py)
resolves missing metadata for upcoming entries. It processes
one entry at a time, outside the Session inbox. Results update existing IDs only,
so removing a track during extraction cannot bring it back. Playback resolution
also saves public metadata before the track starts.

The [media catalog](../backend/src/nahormaar_backend/application/catalog.py) owns
playlist previews and the playlist and metadata caches. `SearchCatalog` owns the
search cache. The [discovery extractor](../backend/src/nahormaar_backend/integrations/discovery.py)
runs at most two
extractors concurrently, with bounded waiting, process output and deadlines.
Playback resolution runs separately. The bounded
[snapshot cache](../backend/src/nahormaar_backend/cache.py) supplies
public metadata immediately and shares background refreshes. Temporary stream
URLs are never cached here.

[Search providers](../backend/src/nahormaar_backend/integrations/youtube_search.py) return the same
track fields. YouTube Music uses `ytmusicapi` with the songs filter; video search
uses `yt-dlp`. Both run in bounded child processes. Search pages use a fixed
snapshot ID, so background updates cannot change pagination midway through
browsing. Search provider selection does not change how audio is resolved for
playback. Cache limits and refresh intervals are listed in the
[discovery API](api.md#find-music).

Playlist previews have four states: `loading`, `ready`, `cancelled` and `failed`.
They collect up to 100 entries, retaining duplicate videos and known unavailable
entries. Each preview belongs to one account; source snapshots and extraction
work can be shared. Cancelling one preview leaves other users' work intact.
Only a confirmed batch reaches the controller. It
appends the selection in one transaction with one queue revision and receipt.

For Opus sources, FFmpeg copies the encoded audio into an Ogg stream. At 100%
volume, compatible 20-ms packets reach Discord unchanged. Lower volume requires
decoding, scaling and Opus encoding with the music profile and a 512-kbit/s target.
Other codecs, packet durations or container gain also require conversion. Output
uses 48-kHz stereo with 20-ms frames.

The audio adapter separates FFmpeg/frame decoding (`integrations/audio_sources.py`)
from bounded buffering and mixing (`integrations/audio_mixer.py`). Each of at most
two sources has a decoder thread and an eight-second buffer. The Discord audio
thread never resolves or opens the next source. Preloading has a 20-second outer
deadline; initial buffering has a 15-second deadline. Failures leave the current
track alone and fall back to ordinary advancement.

`application/crossfade.py` owns preparation keyed by playback attempt, next-entry
ID and setting. A different next entry, seek, stop or disconnect cancels preparation
and reaps its source. Only consumed 20-ms audio frames advance the fade clock.
Complementary cosine gains sum to one, without normalization or limiting. The
overlap is at most the configured duration and half of either track's duration.
Mixing encodes frames with the existing music profile; compatible packets outside
the overlap still pass through unchanged at unity volume. Tail cleanup runs off
the audio thread and finishes before preparing a third track.

Volume changes take effect within the running stream. The decoder follows the
original packets even during passthrough, so lowering the volume needs no new
extraction, connection or playback attempt. Returning to 100% restores the original
packets when the source supports passthrough.

Seeking restarts FFmpeg at the requested offset using the resolved stream URL.
It creates a new playback attempt, so callbacks and controls for the old stream
cannot affect the new one. Volume and pause state stay unchanged. Opus packets
before the target are discarded without re-encoding the remaining stream.

Transient extraction or stream failures get one fresh resolution at the attempt's
starting offset (zero for a new track, the saved position for a restored track).
A failed track is then skipped, with its entry ID and error
available through `PlaybackStatus.last_issue`. A database failure stops audio and
blocks further controls until restart. The last committed snapshot remains intact;
`last_issue.fatal` reports that playback has halted. Other operational failures
persist `error` when the database is available. An unexpected Discord client exit
ends the owning runtime and triggers cleanup.

Voice has its own FSM: `disconnected` -> `connecting` -> `connected`. A failed join,
disconnect or channel change returns an interrupted track to the front of the
queue. Reconnecting after an explicit leave or a lost voice connection requires
a manual playback start; these actions clear the restart checkpoint. Process
shutdown instead retains it. Attempt IDs are always new runtime values.

Stop, disconnect and shutdown cancel extraction and reap the owned child processes.
FFmpeg cleanup completes before another audio source starts. The controller owns
accepted operations until they finish, even if their awaiting caller is cancelled.

## API and simultaneous changes

FastAPI owns one runtime through its lifespan. HTTP controls and playback
callbacks share the Session inbox. The API reads complete committed
snapshots; player endpoints leave database and Discord changes to the controller.

SQLite stores track metadata, contributor profiles, playback history, accounts,
appearance preferences, sessions and pending logins.
`revision` orders visible state
changes, including runtime-only changes such as volume. `queue_revision` changes
only when upcoming entries or their order change. Startup advances the global
revision because the voice connection and other runtime values reset.

Reorder and clear compare the client's queue revision inside the inbox handler.
Playback controls compare the expected playback attempt ID. A late skip for a
finished track cannot consume its successor.

Each mutation reserves its request ID before it runs. Queue edits commit their
outcome in the same transaction as the queue and revisions. Retrying an ID with
the same command from the same account returns its stored outcome and a fresh
snapshot; reusing it for another command or account fails. Receipts have no
automatic expiry.

Discord effects cannot share a SQLite transaction. If the process exits before
their outcome is saved, recovery marks the request as interrupted. It does not
execute the request again. The client reads the current state before deciding
whether to submit a new request ID.

The controller publishes committed snapshots through
[`application/events.py`](../backend/src/nahormaar_backend/application/events.py).
[`api/events.py`](../backend/src/nahormaar_backend/api/events.py) handles SSE
serialization, heartbeats and access rechecks.
Subscribers pass an inbox read barrier, then register with the latest committed
snapshot on the event loop without yielding. Each subscriber buffers at most one
snapshot, replacing an older pending update when needed. Reconnect always starts
with the current state.
The server closes event streams before draining HTTP requests during shutdown.
Failed database writes close streams and make API requests return 503 until
restart.

Playback progress uses position and timestamp anchors updated on playback
transitions. Displaying elapsed time needs no per-second database writes.

See the [API contract](api.md) for requests, responses and conflict handling.

## Accounts and access

FastAPI owns Discord OAuth2, requesting only `identify`. Authlib handles the
authorization-code exchange with PKCE. A pending login stores hashed state and
a browser binding; consuming it is atomic and it expires after ten minutes.
Discord tokens are used to read the user ID and are not retained.

SQLAlchemy stores accounts and seven-day sessions in the player database using
separate, short transactions. The browser receives an opaque HTTP-only cookie;
the database stores its hash. Each account has a stable internal UUID, Discord
ID, chosen name and Pixabot avatar. Migrating older databases preserves queue
and history attribution without claiming those contributors for a new account.

An API boundary checks the session and the current `access.toml` whitelist for
every request, including discovery and live updates. Mutations also require the
configured origin and a session-bound CSRF token. SSE rechecks access before
each snapshot and every two seconds while idle. Logout or revoked access closes
the stream but never controls playback. Playlist previews belong to their creator.

## Dashboard

The Nuxt dashboard controls playback, volume, voice channels and the shared queue.
Its server forwards HTTP requests and SSE to FastAPI on localhost. The proxy
checks the configured public origin, forwards session cookies and CSRF headers,
and relays OAuth redirects without following them. Forwarded host headers cannot
choose a redirect or backend destination.

The browser accepts snapshots by revision and disables mutations while
disconnected. Reorder and clear use the displayed queue revision; playback
actions use the displayed playback ID. A lost response leaves the request ID
and payload available for a safe retry. Closing the dashboard only closes its
event stream; playback continues.

Queue uses one input for video links, playlist links and text searches. A video
link with a playlist parameter keeps its single-track action and offers an
explicit playlist preview. Search and playlists share a side panel, full-screen
on phones, with one scrollable list and a fixed import action. Closing the panel
preserves input, selection and scroll position. New searches supersede old
responses. Background changes remain pending until the user accepts them;
playlist selection follows track identity, with ambiguous duplicates left
unselected. Imports use the same lost-response retry flow as other queue edits.

SortableJS handles queue dragging, including touch input. The dashboard keeps
the displayed order stable during a drag and submits the revision from its start.
Moving to a numbered position uses the same revision check. Conflicting edits
restore the backend's current order.

Thumbnails come from track metadata or the YouTube video ID. Cover and Video fill
the player area, with a dark, blurred overlay behind the track details;
the settings button reveals the native YouTube controls and the full video frame.
The embed first loads while the Video view is visible and connected, initially muted.
Switching to Queue, hiding the browser tab or losing the connection pauses the preview
and keeps the embed. Returning aligns it with the bot's current position and play/pause
state. Track changes in the background load a new embed only when the player becomes
visible again. Selecting Cover or leaving the player page destroys the embed.
The native YouTube controls affect only the browser preview. It follows bot play/pause
changes, but does not synchronize viewers or compensate for Discord audio latency.

At the end of the video, the preview returns to Cover. YouTube's `rel=0` setting
limits recommendations to the same channel; it does not disable them. End cards
during playback can still appear.

Recently played groups matching video IDs and shows their playback count within
the stored 100 starts. Each group uses its latest entry and timestamp; the raw
history remains available for activity statistics.

The dashboard waits for a valid session before connecting to player state.
Session loss clears private state and disables controls; other tabs refresh their
session on logout or profile changes. Old locally stored names and avatars are
first-login suggestions only. The Overview uses retained playback history,
not lifetime listening statistics.

See the [roadmap](../ROADMAP.md) for upcoming work.

# Architecture

Parent: [Project README](../README.md)

## Backend modules

- `domain/` defines immutable values, commands and FSM transitions without
  database, HTTP or Discord dependencies
- `application/` coordinates playback, identity and discovery. The controller
  serializes mutations; its worker owns the synchronous player and database
- `api/` owns FastAPI routes, request and response schemas, authentication checks
  and SSE transport
- `persistence/` owns SQLAlchemy mappings, repositories and Alembic migrations
- `integrations/` handles Discord voice and OAuth, YouTube extraction and child
  processes

`runtime.py` starts and closes the voice client and player together. The API
lifespan owns that runtime and the authentication service. Configuration stays
in `config.py`.

## Queue and player state

The Python core manages one current track and an ordered queue. Each entry has
its own UUID, so adding the same video twice produces independently editable
entries. Snapshots and entries are immutable.

One synchronous `Player` owns the state. Queue operations address entry IDs;
removing or moving the current track requires a playback action instead.
Clearing the queue leaves the current track alone. Stopping puts it first in
the queue and waits for a new start from the beginning.

Playback follows a deterministic finite-state machine. The
[transition table](../backend/src/nahormaar_backend/domain/fsm.py) defines every allowed
state/event pair:

| State     | `play`    | `ready`   | `pause`  | `skip` | `stop` | `fail`  | `finished` |
| --------- | --------- | --------- | -------- | ------ | ------ | ------- | ---------- |
| `idle`    | next      | -         | -        | `idle` | `idle` | -       | -          |
| `loading` | -         | `playing` | -        | next   | `idle` | `error` | -          |
| `playing` | -         | -         | `paused` | next   | `idle` | `error` | next       |
| `paused`  | `playing` | -         | -        | next   | `idle` | `error` | next       |
| `error`   | `loading` | -         | -        | next   | `idle` | -       | -          |

`next` takes the next queued entry into `loading`, or becomes `idle` when the
queue is empty. `play` from `error` retries the same entry. A dash means the
event raises `InvalidTransitionError` without changing state. `finished` also
applies while paused because a pause can race with the final audio frame.
The `seek` event preserves `playing` or `paused` and leaves the queue unchanged;
it is invalid in other states.
The `crossfade` event is valid only while playing with an upcoming entry. It
advances directly to `playing`; the incoming track and its history record commit
together before activation. If EOF wins during that commit, the same incoming
entry starts conventionally, without recording another play.

SQLAlchemy defines the tables and handles database access; Alembic applies schema
migrations at startup. Existing databases are adopted using their legacy version
marker. Migration and stored-data validation share a transaction, so invalid data
leaves the original database unchanged.

Each persistent player change
commits in one Session transaction before the Player publishes its new snapshot.
Failed writes and failed commits leave the previous snapshot intact.

The last 100 started tracks are stored with timestamps and independent history
IDs. Recording a start and entering `playing` share one transaction. Skips keep
the history entry; removing an unplayed track creates none. Resuming or retrying
a stream automatically, or seeking within it, does not count twice.

The singleton `playback_checkpoint` stores the connected channel, current entry
ID, audio position, pause state and volume. Player transactions keep its entry ID
aligned with the queue, resetting position when the current entry changes and
clearing it on an error. The controller refreshes the position every five seconds
and freezes the audio clock before saving on clean shutdown. These writes do not
increment public revisions or emit SSE updates.

Creating a Player with a checkpoint retains the current entry in `loading`.
Once the Discord gateway is ready, the controller rejoins the saved channel and
resolves a fresh stream at that offset. Paused playback starts silently and stays
paused. Existing history identifies a resumed play, so it is not counted again.
A shutdown during startup preserves a checkpoint that has not been restored yet.
During a crossfade the position belongs to the incoming track; its outgoing tail
is not restored. Radio replenishment does not restart automatically.

Without a checkpoint, the FSM's `recover` event returns an interrupted track to
the front of the queue, idle and disconnected. This also handles databases from
before checkpoint support. A failed channel restoration reports a playback issue
and preserves the queue for manual connection. Unknown schemas and invalid stored
states raise `StorageError` without replacing the database.

## Playback and voice

The [PlaybackController](../backend/src/nahormaar_backend/application/playback.py) serializes
controls and callbacks. One worker thread owns the synchronous Player and its
database; Discord runs on the asyncio event loop. Each playback attempt has its
own ID. Late extraction results and completion callbacks are ignored once that
attempt has been stopped, skipped or replaced.

The Discord adapter discovers joined servers and their voice channels from
Discord's guild cache. No guild ID is configured. All servers share the same
queue, with one active voice connection. Switching servers disconnects the old
channel before joining the new one and waits for a manual playback start.
Late disconnect events from the old channel cannot close the new connection.

The [YouTube resolver](../backend/src/nahormaar_backend/integrations/youtube.py) runs `yt-dlp`
in a cancellable child process with a 30-second deadline. It resolves one finite,
public video immediately before playback and selects the best available audio.
Stream URLs, codec information and HTTP headers stay in memory.

The [metadata task](../backend/src/nahormaar_backend/application/metadata.py)
resolves missing metadata for upcoming entries. It processes
one entry at a time, outside the command lock. Results update existing IDs only,
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
callbacks share the controller's lock. The API reads complete committed
snapshots; player endpoints leave database and Discord changes to the controller.

SQLite stores track metadata, contributor profiles, playback history, accounts,
appearance preferences, sessions and pending logins.
`revision` orders visible state
changes, including runtime-only changes such as volume. `queue_revision` changes
only when upcoming entries or their order change. Startup advances the global
revision because the voice connection and other runtime values reset.

Reorder and clear compare the client's queue revision under the controller lock.
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
Subscribers register and receive their first snapshot under the same lock
as publication. Each subscriber buffers at most one snapshot, replacing an older
pending update when needed. Reconnect always starts with the current state.
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

# NaHörMaar roadmap

Parent: [Project README](README.md)

Status: phases 1, 3, 4 and 5 complete. Live playback failure and disconnect checks
passed, as did concurrent queue additions in two browser tabs. Phase 6 is
implemented. Real Discord sign-in, shared-session logout in two tabs and live
whitelist removal have passed locally. Shared deployment and the visible
presence check remain open.

## Current scope

- Server selection from the bot's joined servers, one shared queue and one active voice channel
- YouTube links, search and playlists, controlled entirely through a Nuxt
  dashboard. No Discord chat or slash commands
- The queue survives restarts. Playback resumes only after someone starts it
- Several people can add, remove and reorder tracks at the same time
- Discord login and a configured whitelist control dashboard access

## Proposed design

The Python backend owns the queue, playback and Discord connection. FastAPI and
the Discord client run in one backend process. SQLAlchemy stores queue entries
and their order in SQLite; temporary audio URLs are resolved when needed.

The dashboard sends actions over HTTP and receives state updates through
[Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/).
Each update carries a revision. On connection or reconnection, the backend sends
a complete snapshot so every browser starts from the same state.

Until the login phase is complete, the application binds to localhost. Concurrent
use can be tested with several browser tabs. Shared deployment follows the
whitelist checks in phase 6.

## 1. Queue and persistent player state

- [x] Give each queue entry its own ID, separate from the YouTube video ID.
  Adding the same video twice intentionally creates two entries
- [x] Store the source URL, title, uploader, duration and thumbnail when available.
  Missing metadata must not prevent an entry from being displayed
- [x] Add, remove, reorder and clear upcoming tracks. Keep the current track
  separate from the upcoming queue
- [x] Define playback states: idle, loading, playing, paused and error, with
  explicit FSM events and allowed transitions. Track the Discord connection
  separately
- [x] Persist queue changes atomically before acknowledging them. After a
  restart, put the interrupted track first and wait for a manual start from
  its beginning

| Control       | Behavior                                                                               |
| ------------- | -------------------------------------------------------------------------------------- |
| Play / resume | Start the next track, or resume a paused track                                         |
| Pause         | Keep the current track and its position                                                |
| Skip          | Discard the current track and advance once                                             |
| Stop          | Stop audio and put the current track first; the next start plays it from the beginning |
| Clear queue   | Remove upcoming tracks; leave the current track playing                                |

Acceptance: queue order and IDs survive a restart. Stop and clear have distinct
effects. Tests cover an empty queue, repeated videos and an interrupted track.

## 2. YouTube streaming and Discord voice

- [x] Discover the bot's servers and their available voice channels automatically,
  join a selected channel and leave it on request. Report missing permissions
  and connection failures
- [x] Resolve a YouTube audio stream with `yt-dlp`, feed it to FFmpeg and send
  audio through `discord.py`. Playback starts without downloading the whole
  track first. Closing the dashboard does not stop playback
- [x] Resolve stream URLs shortly before playback and refresh an expired URL
  within a bounded retry. Run extraction outside the API event loop so a slow
  request does not freeze controls
- [x] Implement the player controls above, volume and automatic advancement.
  A late completion callback from a stopped or skipped track must not advance
  the queue again
- [x] Show the current title and uploader in the bot's activity, with paused and
  idle statuses. Coalesce rapid changes within Discord's presence update limit
- [x] Set a daily quote as the bot's bio, keeping the same selection after a restart
- [x] Report unavailable tracks and advance once to the next playable entry.
  A lost Discord connection stops playback and preserves the interrupted track
  for manual restart. Leaving a channel also preserves the queue
- [x] Clean up FFmpeg processes on stop, failure and shutdown. Verify the voice
  dependencies support [DAVE](https://discordpy.readthedocs.io/en/stable/whats_new.html#v2-7-0)
  and install the [YouTube extraction dependencies](https://github.com/yt-dlp/yt-dlp#dependencies),
  including EJS and a supported JavaScript runtime

Acceptance: two YouTube tracks play consecutively in a live test on the configured
Discord server. Pause, resume, skip, stop and volume work during playback. A failed
track, a voice disconnect and shutdown leave no orphaned audio process. Verify
extraction and playback from the intended deployment host before shared use.

Discord bot activities support text but not thumbnail assets or progress bars.
See the [activity field restrictions](https://docs.discord.com/developers/events/gateway-events#activity-object).

## 3. API and simultaneous changes

- [x] Expose queue operations, player controls, channel selection and a state
  snapshot through FastAPI. Route every state change, including playback
  callbacks, through the same player service
- [x] Apply queue mutations in order. Concurrent additions both survive;
  their order follows successful backend commits
- [x] Address entries by ID. Reorder and clear requests include the queue
  revision they were based on. Reject a stale request with the current state
  instead of overwriting someone else's changes
- [x] Give mutations request IDs so retrying a request cannot add or skip twice.
  Playback actions also identify the playback instance they target: two people
  skipping the same track must not skip its successor
- [x] Broadcast committed state changes to every connected dashboard. Reconnect
  with a fresh snapshot; ignore older revisions. Persist revisions with state
  changes so they remain ordered across restarts. Keep progress timestamps
  separate from queue revisions

Acceptance: concurrent add/remove/reorder requests lose no accepted additions
and never affect the wrong entry. Duplicate requests have one effect. A skip
racing with natural track completion advances once. Reconnected clients agree
with the backend without reloading the page.

## 4. Search, playlists and metadata

- [x] Accept YouTube video and playlist links, including short links, plus text
  searches. Validate supported sources before extraction
- [x] Return search results with title, uploader, duration and thumbnail, and
  support adding selected results. Load more in pages of 10, up to 100 results
- [x] Search YouTube Music songs by default, with an explicit Videos option and
  separate providers behind a shared search interface
- [x] Cache search results for five minutes and share identical concurrent lookups
- [x] Detect YouTube playlist links and open a playlist preview. Let users select
  individual tracks or queue the entire playlist in source order, with unavailable
  entries and import limits shown explicitly
- [x] Bound search results, playlist imports and concurrent extraction work.
  Expose import progress, cancellation and partial failures through the API
- [x] Keep metadata separate from temporary stream URLs. A delayed metadata
  result must not recreate an entry someone has already removed

Acceptance: links, search and playlists produce usable queue entries. A playlist
keeps its source order while another user adds a track. Unavailable videos and
missing thumbnails do not abort an otherwise valid import.

## 5. Control dashboard

- [x] Build the Nuxt dashboard around the current track, upcoming queue, search
  and voice channel selection. Let users select search results and preview
  playlists before adding tracks
- [x] Show thumbnails, song and artist links, duration and playback progress. Use a
  fallback image when artwork is missing or fails to load
- [x] Add playback controls, volume, queue removal and reordering. Reordering
  works with drag and drop as well as keyboard controls
- [x] Support touch dragging and moving directly to a numbered queue position,
  with position numbers visible on phones
- [x] Show both the Discord server and voice channel in connection controls
- [x] Show pending actions and explain rejected changes. When the connection
  drops, mark the view as disconnected and disable mutations until state has
  synchronized again
- [x] Restore the Overview with statistics from playback history
- [x] Choose an account name and a random Pixabot avatar, with profile editing
- [x] Show who added each track, with their name and avatar saved in the queue and history
- [x] Give the player a cover-led dark layout, recent artwork and persistent playback
  controls across pages, with light mode and mobile layouts
- [x] Show approximate queue wait times while playing when preceding durations are known,
  using hours and minutes for waits of an hour or more
- [x] Offer full-area Cover and YouTube Video views with native video controls,
  an artwork fallback and no video loading outside the visible Video view
- [x] Keep a footer with project, license and avatar credits in the sidebar,
  including searchable dependency licenses and downloadable notices
- [x] Add short page and Player/Queue transitions that respect reduced motion
  and preserve the video instance when switching tabs
- [x] Save appearance settings per account and ask before loading YouTube media,
  with cookie preferences accessible from the footer
- [x] Group the last 100 started tracks under the queue with play counts, show five
  songs at first and let users requeue them. Keep skipped tracks, but exclude unplayed removals
- [x] Make the controls usable on phones. Distinguish an empty queue, loading,
  paused playback, unavailable media and a disconnected bot

Acceptance: two browsers can control the same session and see each other's
changes. A stale reorder reports a conflict without silently changing the
queue. Refreshing or closing either browser leaves playback intact.

## 6. Login, whitelist and shared use

- [x] Add [Discord OAuth2 login](https://docs.discord.com/developers/topics/oauth2)
  with server-side sessions and a whitelist of Discord user IDs
- [x] Check access in the backend for API requests and live updates. Logging in
  does not grant access unless the account is whitelisted
- [x] Initially, every whitelisted user can control playback and edit the whole
  queue. Manage the whitelist through configuration
- [x] Record who added an entry using the authenticated Discord identity. Entries
  created during local development may have no author
- [x] Support logout, session expiry and whitelist removal. Use HTTP-only session
  cookies and protect state-changing requests against cross-site submission

Acceptance: allowed users can control the bot together. Logged-out, unlisted and
removed users cannot control it through direct API requests or retain access to
live updates. Session expiry is visible in the dashboard.

Verified locally with a real Discord account through the configured dashboard
origin: sign-in, saved profile, logout across two tabs and whitelist removal
while idle. Removal revoked the session in 1.6 seconds and cleared the dashboard.
Concurrent use by separate Discord accounts still needs a live check.

## Queue and discovery improvements

- [x] Keep the queue as the main view. Move search results and playlist imports
  into a shared side panel on desktop and a full-screen view on phones, with
  one scrollable result list and a reachable playlist selection action
- [x] Reduce list dividers and use spacing, typography and subtle hover states
  to separate entries. Keep Recently played secondary to the upcoming queue
- [x] Show cached search results, playlist contents and link metadata immediately,
  then refresh them in the background when needed. Share identical lookups and
  bound cache size and refresh frequency
- [x] Offer updated results without rearranging the list during selection.
  Preserve playlist selections across updates and leave newly discovered tracks
  unselected. Account for removed and changed entries as well as new ones
- [x] Mark tracks already in the queue and offer to skip them during playlist
  imports, while still allowing intentional repeats
- [x] Undo individual removals and queue clears, restoring entries and their
  order without overwriting changes made by other users
- [x] Replace the playback notice bar with Nuxt UI's toaster. Show the affected
  track, a useful error reason and a retry action where appropriate, and avoid
  repeating the same notification after every state update

Acceptance: adding music does not push the queue out of reach. Background refresh
preserves the current selection and scroll position, and a failed refresh leaves
cached results visible with a clear indication that they could not be updated.

## YouTube Music radio

- [ ] Start a radio from a song or playlist, with a preview of related tracks
  before adding them to the queue
- [ ] Offer automatic replenishment while radio is enabled, giving manually
  queued tracks priority and limiting repeats
- [ ] Let users stop radio without stopping the current track. Check which
  recommendation sources work without a personal YouTube Music login

## Administration

- [ ] Add owner and administrator roles with backend-enforced access to
  dashboard whitelist management
- [ ] Select Discord server members by name when granting access, with user-ID
  entry available when member lookup is unavailable
- [ ] Record which administrator granted or revoked access and when. Define
  whether administrators can manage all grants or only their own before implementation

## Next audio step

- [ ] Add optional crossfade (3 to 7 seconds, off by default), with preloading
  and audio mixing between tracks
- [ ] Keep pause, seek, skip and queue edits consistent during transitions,
  without counting either track twice in playback history

## Unattended playback

- [ ] Define what happens when the voice channel becomes empty: pause or leave
  after a configurable grace period, preserving the queue and cancelling the
  pending action if someone returns
- [ ] Add a sleep timer with a visible remaining time and a cancel action

## Backend cleanup

- [x] Organize backend modules by responsibility: domain models and FSM,
  application services/controllers, API views and schemas, events, media
  integrations, caching and persistence
- [x] Keep routes and response serialization separate from application logic.
  HTTP requests and playback callbacks must use the same state-changing operations
- [x] Separate committed snapshot publication from SSE delivery, preserving revisions,
  reconnect snapshots and access checks
- [x] Give search, playlist and metadata caches explicit ownership and consistent
  rules for freshness, background refresh, concurrent lookups and size limits.
  Keep temporary stream URLs separate from reusable metadata
- [x] Group SQLAlchemy mappings, migrations and database access. Separate stored
  records from domain and API models, with explicit transaction boundaries for
  player state, history and accounts

Acceptance: the public API, FSM transitions, session handling and restart behavior
stay unchanged. Existing databases still migrate correctly, and tests follow the
responsibilities of the reorganized modules.

- [ ] When additional media backends need to be supported, introduce narrow
  ports/protocols for source identification and resolution, and move concrete
  adapter construction out of application services
- [ ] When storage needs an alternative implementation, define a persistence
  contract including its errors, so application services can use either backend

## Playback statistics and recap

- [ ] Extend the Overview with bot-wide and per-user statistics, selectable time
  periods and a personal recap inspired by Spotify Wrapped and YouTube Music Recap
- [ ] Count accepted queue additions per user. Show most-requested and
  most-played tracks and artists separately, along with unique tracks and artists
- [ ] Measure actual playback minutes for the bot and for each user's requests,
  accounting for pauses, seeks, skips and failures. Define personal listening
  time separately; requesting a song does not prove that someone heard it
- [ ] Show activity over time and longest active-day streaks, with explicit
  rules for qualifying activity and timezones
- [ ] Add playful time comparisons, such as books someone could have read or
  kilometres they could have walked, with visible assumptions and approximate values
- [ ] Persist the data needed for statistics independently of the last 100
  recently played entries. Use stable track, artist and user identities where
  available, and handle missing artist metadata without counting uploaders as artists
- [ ] Show when collection began and where older data is incomplete. Decide
  retention and who can view another user's personal statistics before implementation

Acceptance: totals survive restarts and history pruning. Retried requests and
playback callbacks do not inflate counts. Request counts, playback counts and
listening time have distinct meanings, and each recap states its covered period.

## Deployment and recovery

- [ ] Provide a documented deployment path with HTTPS, persistent data and
  automatic process restart after a crash, preserving manual playback resumption
- [ ] Schedule database backups and define retention and a restore procedure
- [ ] Test restoration into a fresh instance, including queue order, account
  profiles and available playback statistics, with credentials configured separately

## Deferred

- [ ] Give each Discord server its own persistent queue, player, history and
  volume, with simultaneous playback across servers
- [ ] Scope access rules, dashboard actions and live updates to the selected server
- [ ] Watch Together in the browser dashboard, with shared video state, synchronized
  playback and a way to recover after reconnecting. No Discord screen sharing
- [ ] Decide how to distinguish music from video for Watch Together. Consider
  preferring YouTube Music for songs and treating ordinary YouTube links as video;
  preserve an explicit choice when that guess is wrong

- Spotify link and playlist import, after YouTube playback works.
- Shuffle, repeat, saved personal playlists and voting.
- YouTube livestreams and media that requires a personal YouTube login.

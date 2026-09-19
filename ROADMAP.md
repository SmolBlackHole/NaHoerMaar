# NaHörMaar roadmap

Parent: [Project README](README.md)

Status: phases 1 and 3 complete. Phase 2 implemented, with live failure and
disconnect checks still pending. The dashboard controls individual YouTube links;
search, playlist imports and login remain open.

## Current scope

- One Discord server, one shared queue and one active voice channel
- YouTube links, search and playlists, controlled entirely through a Nuxt
  dashboard. No Discord chat or slash commands
- The queue survives restarts. Playback resumes only after someone starts it
- Several people can add, remove and reorder tracks at the same time
- Login and a whitelist come after playback and the dashboard work

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

- [x] Connect the bot to the configured server. List available voice channels,
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

- [ ] Accept YouTube video and playlist links, including short links, plus text
  searches. Validate supported sources before extraction
- [ ] Return search results with title, uploader, duration and thumbnail, and
  support adding selected results
- [ ] Detect YouTube playlist links and open a playlist tab. Let users select
  individual tracks or queue the entire playlist in source order, with unavailable
  entries and import limits shown explicitly
- [ ] Bound search results, playlist imports and concurrent extraction work.
  Expose import progress, cancellation and partial failures through the API
- [x] Keep metadata separate from temporary stream URLs. A delayed metadata
  result must not recreate an entry someone has already removed

Acceptance: links, search and playlists produce usable queue entries. A playlist
keeps its source order while another user adds a track. Unavailable videos and
missing thumbnails do not abort an otherwise valid import.

## 5. Control dashboard

- [ ] Build the Nuxt dashboard around the current track, upcoming queue, search
  and voice channel selection. Let users select search results and preview
  playlists before adding tracks
- [x] Show thumbnails, song and artist links, duration and playback progress. Use a
  fallback image when artwork is missing or fails to load
- [x] Add playback controls, volume, queue removal and reordering. Reordering
  works with drag and drop as well as keyboard controls
- [x] Show pending actions and explain rejected changes. When the connection
  drops, mark the view as disconnected and disable mutations until state has
  synchronized again
- [x] Restore the Overview with statistics from playback history
- [x] Choose a browser profile name and a random Pixabot avatar, with profile editing
- [x] Show who added each track, with their name and avatar saved in the queue and history
- [x] Give the player a cover-led dark layout, recent artwork and persistent playback
  controls across pages, with light mode and mobile layouts
- [x] Show approximate queue wait times while playing when preceding durations are known
- [x] Offer full-area Cover and YouTube Video views with native video controls,
  an artwork fallback and no video loading outside the visible Video view
- [x] Keep a footer with project, license and avatar credits in the sidebar
- [x] Group the last 100 started tracks under the queue with play counts, show five
  songs at first and let users requeue them. Keep skipped tracks, but exclude unplayed removals
- [x] Make the controls usable on phones. Distinguish an empty queue, loading,
  paused playback, unavailable media and a disconnected bot

Acceptance: two browsers can control the same session and see each other's
changes. A stale reorder reports a conflict without silently changing the
queue. Refreshing or closing either browser leaves playback intact.

## 6. Login, whitelist and shared use

- [ ] Add [Discord OAuth2 login](https://docs.discord.com/developers/topics/oauth2)
  with server-side sessions and a whitelist of Discord user IDs
- [ ] Check access in the backend for API requests and live updates. Logging in
  does not grant access unless the account is whitelisted
- [ ] Initially, every whitelisted user can control playback and edit the whole
  queue. Manage the whitelist through configuration
- [ ] Record who added an entry using the authenticated Discord identity. Entries
  created during local development may have no author
- [ ] Support logout, session expiry and whitelist removal. Use HTTP-only session
  cookies and protect state-changing requests against cross-site submission

Acceptance: allowed users can control the bot together. Logged-out, unlisted and
removed users cannot control it through direct API requests or retain access to
live updates. Session expiry is visible in the dashboard.

## Deferred

- [ ] Watch Together in the browser dashboard, with shared video state, synchronized
  playback and a way to recover after reconnecting. No Discord screen sharing
- [ ] Decide how to distinguish music from video for Watch Together. Consider
  preferring YouTube Music for songs and treating ordinary YouTube links as video;
  preserve an explicit choice when that guess is wrong

- Spotify link and playlist import, after YouTube playback works.
- Multiple Discord servers or simultaneous voice channels.
- Seeking, shuffle, repeat, saved personal playlists and voting.
- YouTube livestreams and media that requires a personal YouTube login.

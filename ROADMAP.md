# NaHörMaar roadmap

Parent: [Project README](README.md)

This file records the next meaningful NaHörMaar milestones. It is direction,
not a completion ledger or a second description of the current product.

Implemented behavior belongs in the [documentation index](docs/README.md).
Concrete request and event shapes belong in the [Engine API](docs/engine-api.md),
and current listening evidence belongs in
[Testing and acceptance](docs/testing.md#live-acceptance). Completed work leaves
this page once implementation, tests and its owning documentation agree.

## Next: performance profiling and Player startup

Profile the application after the current Library and playlist plan is complete,
starting with the Player page's noticeably long loading time. Establish separate
cold-load, warm-load and in-app navigation baselines on desktop and mobile before
changing implementation details.

Use browser timings, the network waterfall and server measurements to distinguish
API latency, payload size, Nuxt hydration, component rendering, artwork or video
loading and live-state connection startup. Keep cached content visible during
refreshes, load only the media and UI required by the selected Player view and
avoid making the initial page wait for optional controls or background data.

Fix the measured bottlenecks in small slices and record the before-and-after
numbers. Extend the pass to other slow routes only after the Player path is
understood, so shared improvements are extracted from real repetition instead of
speculative caching, prefetching or new infrastructure.

## Later: shared and synchronized playlists

Extend personal playlists with explicit collaborators and private,
collaborator-only or public visibility. Keep the creator as owner and define
rename, editing, sharing and removal permissions before exposing another user's
playlist on profiles or server pages.

A playlist may remain a local copy or become linked to YouTube Music or YouTube.
Store provider identity, playlist identity and reusable track metadata; resolve
temporary audio sources only when playing.

Show cached contents immediately, refresh on open and before queueing, and allow
bounded periodic synchronization. Reconcile additions, removals, reordering and
intentional duplicates while preserving the listener's current selection. Keep
the last successful contents when a source is unavailable, show the last sync,
and provide an explicit way to detach a linked playlist as a local copy.

Define how local edits and upstream changes interact before implementation.
Refreshing a saved playlist must not rewrite tracks that are already queued or
playing.

## Later: faithful loading states

Replace generic loading bars and spinners with skeletons that match the content
they introduce. API-backed pages and components should reserve the final layout,
including its spacing, hierarchy and common item count, so loaded data does not
make the interface jump.

Skeletons belong to the first load while the content is still unknown. Cached or
stale data should remain visible during a refresh, and empty, failed and denied
requests need their own honest states. Reuse the same layout primitives as the
finished components, expose the loading state to assistive technology and keep
motion subtle or disabled when reduced motion is requested.

## Later: runtime health and capacity

Add lightweight self-monitoring for CPU and memory use, event-loop lag, database
pool pressure, FFmpeg preparation time and playback stalls. Keep short rolling
measurements and surface actionable failures in diagnostics without turning the
bot into a separate monitoring platform.

Use those measurements to document realistic minimum hardware only after a
representative listening run. A Raspberry Pi or small VPS recommendation should
name the tested workload, audio path and concurrent dashboard activity.

## Later: independent Discord sessions

Give each Discord server its own persistent queue, player, history, volume and
access scope, with simultaneous playback across servers. Dashboard actions and
live events must target the selected server. Saved server playlists and reaction
scope depend on this boundary.

A later Watch Together mode may synchronize browser video without depending on
Discord screen sharing. It needs explicit recovery after reconnect and a clear
choice between music and video behavior. YouTube Music may remain the default for
songs while an ordinary YouTube link keeps an explicit video choice.

## Later directions

- Decide whether reactions influence Radio, recommendations or personal
  statistics. Automatic skipping remains a separate opt-in product decision.
- Add shuffle, repeat and voting after their interaction with manual requests,
  Radio and server-specific queues is defined.
- Consider YouTube livestreams and media that requires a personal YouTube login
  only after credentials, refresh and failure behavior have an explicit owner.
- Introduce a persistence protocol only when a real second storage implementation
  exists and its errors and transaction guarantees are known.

# NaHörMaar roadmap

Parent: [Project README](README.md)

This file records the next meaningful NaHörMaar milestones. It is direction,
not a completion ledger or a second description of the current product.

Implemented behavior belongs in the [documentation index](docs/README.md).
Concrete request and event shapes belong in the [Engine API](docs/engine-api.md),
and current listening evidence belongs in
[Testing and acceptance](docs/testing.md#live-acceptance). Completed work leaves
this page once implementation, tests and its owning documentation agree.

## Next: API contract and queryability review

Review the complete HTTP surface after the current TODO is finished. Consolidate
resource routes that currently split browsing, filtering and history into
separate endpoints. Define consistent resource names, HTTP verbs, query
parameters, pagination documents, search behavior, errors and OpenAPI naming.

Queryable collections should support their real use cases through filters,
search and stable pagination instead of adding a new route for every view. Keep
command endpoints only where an operation is not a resource mutation. Record
intentional exceptions and cut contracts over directly with the generated
TypeScript client rather than maintaining parallel compatibility shapes.

## Next: backend model and pattern consolidation

Audit the domain, database models, projections, repositories and services after
the current feature work has settled. Simplify repeated conversions, cursor or
page documents, query parsing and repository plumbing with small shared helpers
only where the same decision is already repeated.

Keep ownership explicit and domain values typed. This is a focused refactor,
not another rewrite: remove accidental duplication and inconsistent patterns
without replacing working module boundaries or introducing generic frameworks.

## Next: lyrics

Add an optional lyrics view for the current track. Prefer synchronized lyrics
and fall back to plain text. Playback position, pause, resume, seek and track
changes must move the active line without making lyrics part of the audio path.
Listeners should be able to scroll away and return to the current line.

Evaluate [LRCLIB](https://github.com/tranxuanthang/lrclib) for coverage,
attribution and caching terms before choosing it. Match on title, artist,
duration and album where available, distinguish remixes and live versions, and
allow another match when metadata is ambiguous. Cache successful matches and
temporary misses with bounded retention. Instrumental tracks, missing lyrics and
provider failures need different states, and an old result must never appear for
the next song.

## Later: reactions

Let each user like or dislike a persistent track, change the reaction or remove
it. Show totals and the people behind them on demand. Reactions belong to stable
track and account identities, not queue entries, so they survive requeueing and
can contribute to personal statistics.

Automatic skipping and recommendation filtering remain separate product
decisions. Define whether reactions become server-specific before independent
server sessions are introduced.

## Later: saved and synchronized playlists

Create personal playlists and shared server playlists with saved order,
ownership and editing permissions. A playlist may be a local copy or remain
linked to YouTube Music, YouTube or a future Spotify importer. Store provider
identity, playlist identity and reusable track metadata; resolve temporary audio
sources only when playing.

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

- Import Spotify tracks and playlists as metadata, then resolve a playable
  equivalent through the catalog instead of treating Spotify as an audio source.
- Add shuffle, repeat and voting after their interaction with manual requests,
  Radio and server-specific queues is defined.
- Consider YouTube livestreams and media that requires a personal YouTube login
  only after credentials, refresh and failure behavior have an explicit owner.
- Introduce a persistence protocol only when a real second storage implementation
  exists and its errors and transaction guarantees are known.

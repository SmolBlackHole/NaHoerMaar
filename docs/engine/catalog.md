# Catalog and metadata

Parent: [Engine documentation](README.md)

The catalog turns provider-specific searches, links and playlists into stable
NaHörMaar tracks. This page owns provider selection, discovery snapshots,
metadata merging and the in-memory discovery cache. Queue behavior belongs in
[Queue and history](queue.md); resolving playable audio belongs in
[Playback](playback.md).

## Table of contents

- [Catalog and metadata](#catalog-and-metadata)
  - [Table of contents](#table-of-contents)
  - [Provider boundary](#provider-boundary)
  - [Tracks and observations](#tracks-and-observations)
  - [Stable discovery results](#stable-discovery-results)
  - [Cache and refresh behavior](#cache-and-refresh-behavior)
  - [Audio resolution](#audio-resolution)

## Provider boundary

Providers recognize their own URLs and translate source-specific results into
domain values. A provider may support search, a single track, playlists,
recommendations and playable audio. The catalog selects a capable provider and
coordinates the operation; it does not know YouTube response shapes.

YouTube Music is the default search provider. Ordinary YouTube video search is
available separately. Both implement the provider protocol in
`catalog/providers.py`; their concrete translation lives in
`integrations/youtube.py`. Adding another source means implementing those
capabilities and composing the provider in `bootstrap.py`, without teaching
queue or playback about its wire format.

## Tracks and observations

A provider finding is an observation of a media source. It is not a queue item.
The catalog resolves the source identity to a persistent track ID, then its
repository merges the observation with known metadata for that track.
New observations can improve a title, artist, duration or artwork without
rewriting queue entries and playback history.

Artist entities require an explicit provider identity. A display name by itself
does not create an artist identity. Metadata keeps provenance per field so a new
observation can be compared with the source that supplied the stored value.
Temporary audio URLs and request headers are playback material, not metadata;
they remain in memory and are never persisted on a track.

The database relationships and repository ownership are documented in
[Database](database.md).

## Stable discovery results

Search and playlist results are exposed as immutable, versioned snapshots.
Every occurrence has a source position and metadata. Available results also have
a media reference and the persistent track ID used when they are added to the
queue; an unavailable result may have neither. Repeated playlist occurrences may
point to the same track and remain separate choices.

The dashboard keeps the version a listener is looking at. A refresh may create
a newer version, but it cannot reorder the visible list or silently change a
playlist selection. Queue additions therefore submit track IDs from the pinned
snapshot rather than interpreting result positions again.

Pagination reads the fixed snapshot; it does not continue fetching an unlimited
upstream result set. The response limit, HTTP shapes and pagination fields are
defined in the
[Engine API](../engine-api.md#discovery-and-stable-selections).

## Cache and refresh behavior

Search observations are fresh for five minutes; playlist observations are fresh
for one minute. The latest three persisted versions remain available so a
listener can finish a selection.

When cached data becomes stale, the catalog can return it immediately while one
shared refresh runs. Identical callers share that work. A changed result gets a
new version; an unchanged result keeps its version. If a refresh fails, the last
known result stays available and the cache backs off before trying again.

Discovery snapshots and canonical track data live in PostgreSQL. Restarting the
backend therefore keeps pinned versions available until normal retention removes
them.

## Audio resolution

When playback needs a track, the catalog loads its stored source reference and
routes it to a provider that can resolve a fresh playable source. This happens
outside the Session transaction because provider and network work may block. The
result returns with the attempt identity that requested it; playback rejects it
if seek, skip, stop or another transition has already replaced that attempt.

The catalog returns technical source material to playback without storing it as
track metadata. Buffering, FFmpeg, Opus and Discord output are described in
[Playback](playback.md).

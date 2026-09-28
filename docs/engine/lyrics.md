# Lyrics

Parent: [Engine documentation](README.md)

The Lyrics module looks up words for an existing Catalog track. It does not
participate in queueing, playback preparation, audio output or the live Player
state. The current track ID is the only link between those concerns.

## Provider boundary

`lyrics/providers.py` owns the provider protocol. The LRCLIB adapter in
`integrations/lrclib.py` identifies NaHörMaar with the required client header,
sends title and artist plus album and duration when known, serializes requests
and observes rate-limit responses. LRCLIB needs no user account or API key.

The provider returns available lyrics, an instrumental result or no match.
Transport errors remain provider failures and are not turned into a missing
lyrics result.

## Cache and matching

One `track_lyrics` row belongs to one canonical Catalog track. Its metadata
signature covers title, credited artists, album and rounded duration. Better
Catalog metadata therefore invalidates an earlier match without changing the
track identity.

Available and instrumental results stay fresh for 30 days. A confirmed miss is
cached for 12 hours. Concurrent lookups for the same track share one provider
request. If LRCLIB fails while a previously successful result exists for the
same metadata signature, the service returns that copy as stale rather than
discarding useful lyrics.

Synchronized LRC is parsed into ordered, timed lines in the backend. Plain text
is split into untimed lines. The dashboard uses the Player position to select
the active timed line; seeking and track changes therefore need no lyrics event
on SSE.

## Dashboard behavior

The Player starts the track-keyed lookup in the background and exposes Lyrics
beside Cover and Video. A newer track cancels the earlier browser request and
clears its result. The view distinguishes loading, available, instrumental,
missing and provider-failure states, attributes LRCLIB and lets a listener
scroll away from synchronized following and return to the current line.

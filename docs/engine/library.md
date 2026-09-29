# Library

Parent: [Engine documentation](README.md)

The Library owns persistent reactions and user-owned playlists for canonical
Catalog tracks. Playlists may stay local or remain linked to a supported
provider playlist. Visibility and collaborators control who may read or edit a
playlist without changing its owner.

## Table of contents

- [Library](#library)
  - [Table of contents](#table-of-contents)
  - [Ownership boundary](#ownership-boundary)
  - [Reactions](#reactions)
  - [Playlists and occurrences](#playlists-and-occurrences)
  - [Visibility and access](#visibility-and-access)
  - [Linked playlists](#linked-playlists)
  - [Reading and pagination](#reading-and-pagination)
  - [Queue integration](#queue-integration)
  - [Maintenance and recovery](#maintenance-and-recovery)
  - [Persistence and cleanup](#persistence-and-cleanup)
  - [Current limits](#current-limits)

## Ownership boundary

`library/` owns reaction and playlist domain values, writes, read models,
permissions, synchronization and maintenance contributions. It references
Catalog track and source identities instead of copying provider observations.
It does not resolve playable media or own queue order. When a listener queues a
saved selection, Library returns stable track selections and Player performs the
queue mutation.

The module is composed through `library/main.py`. Its service receives the
shared unit-of-work factory and Catalog boundary explicitly. The HTTP contract
lives in the [Library section of the Engine API](../engine-api.md#library).

## Reactions

Each user may have one `like` or `dislike` for a track. Setting the other value
replaces the current reaction; removing it leaves no reaction. The Library can
read summaries for up to 100 tracks at once, including the signed-in user's
value and the total likes and dislikes.

Liked and disliked tracks are virtual collections derived from reactions. They
support text search and numbered, snapshot-stable pagination. Authenticated
listeners may also open those collections on another listener's profile.
Participant pages show the users behind one reaction value without exposing
reactions for an unrelated track.

Reactions do not skip tracks, change Radio or rank recommendations. Those remain
separate product decisions on the [roadmap](../../ROADMAP.md).

## Playlists and occurrences

A playlist always belongs to its creator. The owner can create, rename,
duplicate, reorder or delete playlists and may add collaborators or change
visibility. Local playlist entries are occurrences rather than a unique set of
tracks. Duplicate tracks are intentional. Each occurrence stores its
contributor, stable Catalog track and optional preferred source.

A playlist contains at most 1,000 occurrences. One entry mutation accepts at
most 100 selections, and its name contains 1 to 100 trimmed characters. Every
playlist starts at revision 0. A successful metadata, collaborator or entry
mutation increments that revision. Mutations carry `expected_revision`; a stale
editor receives `library_playlist_revision_conflict` instead of overwriting
newer state.

Removing one local occurrence creates a short-lived Undo receipt. Undo restores
that exact occurrence at its previous position when the playlist revision still
matches. Owners can reorder their playlist cards, and owners or collaborators
can drag local entries into a new persistent order.

## Visibility and access

Visibility is `private`, `collaborators` or `public`:

- private playlists are available only to their owner;
- collaborator playlists are available to the owner and invited editors;
- public playlists are readable by every authenticated listener and appear on
  the owner's profile.

The read model exposes `owner`, `editor` or `reader` access. Only the owner may
rename or delete a playlist, change visibility, manage collaborators, move the
owner's playlist card, synchronize a linked source or detach it. Editors may
change entries in a local playlist. Readers may inspect and queue visible
contents but cannot mutate them. Contributor search returns existing listeners
without making Discord membership a second Library identity model.

## Linked playlists

Importing a supported YouTube or YouTube Music playlist stores its provider
identity, canonical URL, ordered occurrences and last successful source state.
The same owner cannot import the same provider playlist twice. Linked contents
are read-only because provider order is authoritative; detach converts the
playlist to a normal local playlist before entries can be edited.

Synchronization reconciles additions, removals, moves and duplicate
occurrences while retaining stable entry IDs where the provider occurrence can
be matched. Unavailable entries and provider truncation are reported on the
source state. A failed or partial provider refresh keeps the last successful
contents and records the failure instead of replacing the playlist with an
incomplete result.

Owners may request a synchronization directly. The Library also contributes an
hourly bounded `playlist-sync` job with a default batch of ten playlists and at
most four provider requests in parallel. The job records per-playlist changes
in the shared Operations run history.

## Reading and pagination

Playlist and reaction collections use the shared numbered-page document:
`items`, `page`, `page_size`, `total`, `page_count` and `snapshot`. The first
page creates an opaque snapshot. Following pages reuse it so changed data does
not move or duplicate items inside the visible result set. Changing search text
or scope starts a new snapshot.

Playlist scopes are owned, shared and public. Profile routes expose only public
playlists and the selected listener's explicit reaction collections. Private
and collaborator-only playlists do not contribute profile list items. Server
Stats excludes private playlist contents from its saved-track ranking while
including playlists shared with collaborators.

Playlist-entry snapshots bind to the playlist ID and revision. A stale revision
conflicts instead of presenting pages from different orders. The dashboard
loads the complete unfiltered entry set for drag ordering because a partial or
filtered page cannot define the whole order safely.

## Queue integration

Queueing saved occurrences preserves their stored order, duplicates and
preferred source selections. A complete playlist can be queued only while it
contains at most 100 occurrences; larger playlists use the dashboard's bounded
selection control. Queue mutations require the current playlist revision and an
`Idempotency-Key`. Player then applies the ordinary authenticated queue command,
including requester attribution and replay protection.

The saved playlist remains unchanged after queueing. Synchronization likewise
does not rewrite tracks already copied into the live queue or currently playing.

## Maintenance and recovery

The Library contributes two bounded maintenance paths. Hourly playlist
synchronization refreshes due linked playlists. General housekeeping removes
expired playlist-entry Undo receipts. Both use the shared Operations job model,
so scheduled and manual runs retain trigger, counts, duration, details and
failure state. A later successful run recovers the job health after a partial or
failed run.

## Persistence and cleanup

`track_reactions`, `playlists`, `playlist_collaborators`, `playlist_entries` and
`playlist_entry_undos` are durable PostgreSQL tables. Playlist source identity
and synchronization state live on the playlist row. Foreign keys restrict
removal of referenced tracks and preferred sources. Catalog cleanup excludes
tracks and sources referenced by a reaction or playlist occurrence before it
locks deletion candidates.

Deleting a user cascades their reactions and owned playlists. Deleting a
playlist cascades collaborators, entries and Undo receipts. A surviving entry's
`added_by` user remains protected by a restricting foreign key.

## Current limits

- A playlist contains at most 1,000 occurrences; one mutation or queue command
  handles at most 100 selections.
- Library changes use HTTP reads and mutations; there is no Library SSE stream.
- Linked playlists are provider-owned snapshots. NaHörMaar does not write
  changes back to YouTube or YouTube Music.
- Linked contents have no local overlay. Detach before editing entries.
- Starting Radio from a saved playlist is not defined yet.

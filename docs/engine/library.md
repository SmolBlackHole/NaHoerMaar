# Library

Parent: [Engine documentation](README.md)

The Library owns persistent reactions and personal playlists for canonical
Catalog tracks. A reaction belongs to one user and one track. A playlist owns
an ordered list of track occurrences, so the same track may appear more than
once and each occurrence may retain its preferred provider source.

## Table of contents

- [Library](#library)
  - [Table of contents](#table-of-contents)
  - [Ownership boundary](#ownership-boundary)
  - [Reactions](#reactions)
  - [Personal playlists](#personal-playlists)
  - [Reading and pagination](#reading-and-pagination)
  - [Queue integration](#queue-integration)
  - [Persistence and cleanup](#persistence-and-cleanup)
  - [Current limits](#current-limits)

## Ownership boundary

`library/` owns reaction and playlist domain values, writes, read models and
service rules. It references Catalog track and source identities instead of
copying provider observations. It does not resolve media or own queue order.
When a listener queues a saved playlist, Library returns the ordered stable
selections and Player performs the queue mutation.

The module is composed through `library/main.py`. Its service receives the
shared unit-of-work factory and Catalog boundary explicitly. The HTTP contract
lives in the [Library section of the Engine API](../engine-api.md#library).

## Reactions

Each user may have one `like` or `dislike` for a track. Setting the other value
replaces the current reaction; removing it leaves no reaction. The Library can
read summaries for up to 100 tracks at once, including the signed-in user's
value and the total likes and dislikes.

Liked and disliked tracks are virtual collections derived from reactions. They
support text search and numbered, snapshot-stable pagination. Participant pages
show the users behind one reaction value without exposing reactions for an
unrelated track.

Reactions do not skip tracks, change Radio or rank recommendations. Those are
separate product decisions on the [roadmap](../../ROADMAP.md).

## Personal playlists

A playlist belongs to its creator. Current reads and writes are owner-scoped;
sharing and collaborators are not part of this release. The owner can create,
rename, duplicate or delete a playlist and add, remove or reorder its entries.

Playlist entries are occurrences rather than a unique set of tracks. Duplicate
tracks are intentional. Each entry stores the contributor, its stable Catalog
track and an optional preferred source. A playlist contains at most 100 entries
and its name contains 1 to 100 trimmed characters.

Every playlist starts at revision 0. A successful rename, entry mutation or
reorder increments that revision. Mutations carry `expected_revision`; a stale
editor receives `library_playlist_revision_conflict` instead of overwriting a
newer order. Reordering must submit every current entry ID exactly once.

## Reading and pagination

Playlist collections and reaction collections use the shared numbered-page
document: `items`, `page`, `page_size`, `total`, `page_count` and `snapshot`.
The first page creates an opaque snapshot. Following pages reuse it so newly
changed data does not move or duplicate items inside the visible result set.
Changing search text or filters starts a new snapshot.

Playlist-entry pages bind their snapshot to the playlist ID and revision. A
stale revision conflicts instead of presenting pages from different playlist
orders. The complete unfiltered entry set is loaded for order editing because a
partial or filtered page cannot define the whole order safely.

## Queue integration

Queueing one playlist preserves its stored order, duplicates and preferred
source selections. The request requires the current playlist revision and an
`Idempotency-Key`. Player then applies the ordinary authenticated queue command,
including requester attribution and replay protection.

The saved playlist remains unchanged after queueing. Queue edits likewise do
not rewrite the saved list.

## Persistence and cleanup

`track_reactions`, `playlists` and `playlist_entries` are durable PostgreSQL
tables. Foreign keys restrict removal of referenced tracks and preferred
sources. Catalog cleanup also excludes tracks and sources referenced by a
reaction or playlist entry before it locks deletion candidates.

Deleting a user cascades their reactions and owned playlists. Deleting a
playlist cascades its entries. An entry's `added_by` user is retained through a
restricting foreign key while the entry exists.

## Current limits

- Playlists are private to their owner.
- A playlist contains at most 100 occurrences.
- Library changes are HTTP reads and mutations; there is no Library SSE stream.
- Provider playlists can be previewed and selected through Catalog, but a saved
  playlist is not linked to or synchronized with its provider source.
- Starting Radio from a saved playlist is not defined yet.

Shared playlists, visibility, collaborator permissions and linked provider
synchronization remain on the [roadmap](../../ROADMAP.md).

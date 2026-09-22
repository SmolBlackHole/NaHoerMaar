# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist provider observations through one shared metadata write boundary."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .domain.catalog import TrackFinding
from .domain.metadata import MetadataKind, MetadataSource
from .domain.tracks import Artist, MediaIdentity, Track
from .observability import logged_operation
from .persistence import ArtistRepository, TrackRepository, write_transaction


class MetadataStore:
    """Shared by the catalog's discovery and playback resolution paths.

    Supply observations after provider I/O finishes. Each batch commits atomically
    and borrows its own database session. The composition root shares one instance
    for metadata writes; the lock prevents concurrent read/insert races on SQLite.
    It owns neither the database pool nor provider tasks and has no worker loop.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._writes = asyncio.Lock()

    async def get(self, track_id: UUID) -> Track:
        async with self._sessions() as session, session.begin():
            track = await TrackRepository(session).get(track_id)
        if track is None:
            raise LookupError("Unknown catalog track.")
        return track

    async def get_many(
        self, identifiers: tuple[UUID | MediaIdentity, ...]
    ) -> tuple[Track, ...]:
        async with self._sessions() as session, session.begin():
            return await TrackRepository(session).get_many(identifiers)

    @logged_operation("engine.metadata.remember")
    async def remember(
        self, findings: tuple[TrackFinding, ...], *, source: MetadataSource
    ) -> tuple[Track, ...]:
        """Return final catalog records in input order, including duplicates.

        Only available, normalized tracks enter here. An unavailable playlist
        occurrence stays in its result page; it does not erase a known track.
        Re-observation preserves the initial public source reference and identity.
        """
        if not findings:
            return ()
        async with self._writes:
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("Metadata storage time must be timezone-aware.")
            now = now.astimezone(UTC)
            remembered: dict[MediaIdentity, Track] = {}
            async with write_transaction(self._sessions) as session:
                tracks = TrackRepository(session)
                artists = ArtistRepository(session)
                for finding in findings:
                    identity = finding.reference.identity
                    previous = remembered.get(identity) or await tracks.find(identity)
                    track = previous or Track(
                        identity,
                        finding.reference.source_url,
                        created_at=now,
                        updated_at=now,
                    )
                    metadata, provenance = track.metadata.merge_observation(
                        finding.metadata, track.provenance, source
                    )
                    artist_ids = track.artist_ids
                    if finding.artists is not None and (
                        (not artist_ids and provenance.artists is None)
                        or source.supersedes(provenance.artists)
                    ):
                        credits: list[UUID] = []
                        for credit in finding.artists:
                            artist = await artists.find(credit.identity)
                            if artist is None:
                                artist = Artist(
                                    credit.identity, credit.name, name_source=source
                                )
                                await artists.add(artist)
                            elif source.supersedes(artist.name_source):
                                artist = replace(
                                    artist, name=credit.name, name_source=source
                                )
                                await artists.update(artist)
                            credits.append(artist.id)
                        artist_ids = tuple(credits)
                        provenance = replace(provenance, artists=source)
                    checked_at = track.checked_at
                    if source.kind is MetadataKind.DETAIL:
                        observed_at = source.observed_at.astimezone(UTC)
                        checked_at = (
                            max(checked_at.astimezone(UTC), observed_at)
                            if checked_at is not None
                            else observed_at
                        )
                    changed = (
                        metadata != track.metadata or artist_ids != track.artist_ids
                    )
                    track = replace(
                        track,
                        metadata=metadata,
                        provenance=provenance,
                        artist_ids=artist_ids,
                        checked_at=checked_at,
                        updated_at=max(now, track.updated_at.astimezone(UTC))
                        if changed
                        else track.updated_at,
                    )
                    if previous is None:
                        await tracks.add(track)
                    elif track != previous:
                        await tracks.update(track)
                    remembered[identity] = track
            return tuple(remembered[finding.reference.identity] for finding in findings)

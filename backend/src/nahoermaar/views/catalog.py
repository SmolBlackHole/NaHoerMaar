# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Read-only cross-module projections used by Catalog maintenance."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import exists, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from nahoermaar.catalog.domain import (
    ArtistId,
    ProviderName,
    TrackId,
    TrackSourceId,
)
from nahoermaar.database.schema import registered_table


@dataclass(frozen=True, slots=True)
class CatalogCleanupSource:
    id: TrackSourceId
    track_id: TrackId
    title: str
    provider: ProviderName
    external_id: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class CatalogCleanupTrack:
    id: TrackId
    title: str


@dataclass(frozen=True, slots=True)
class CatalogCleanupArtist:
    id: ArtistId
    name: str


@dataclass(frozen=True, slots=True)
class CatalogCleanupCandidates:
    sources: tuple[CatalogCleanupSource, ...]
    tracks: tuple[CatalogCleanupTrack, ...]
    artists: tuple[CatalogCleanupArtist, ...]


class CatalogCleanupReader(Protocol):
    """Read contract required by Catalog cleanup."""

    async def candidates(
        self,
        session: AsyncSession,
        *,
        checked_before: datetime,
        limit: int,
    ) -> CatalogCleanupCandidates: ...


class CatalogCleanupView:
    """Find old Catalog rows that have no durable use anywhere in the app."""

    __slots__ = (
        "_artists",
        "_discovery_results",
        "_playlist_entries",
        "_radio_candidates",
        "_radio_exclusions",
        "_radio_runs",
        "_reactions",
        "_requests",
        "_source_artists",
        "_sources",
        "_track_artists",
        "_tracks",
    )

    def __init__(self) -> None:
        self._artists = registered_table("artists", consumer="Catalog cleanup")
        self._tracks = registered_table("tracks", consumer="Catalog cleanup")
        self._sources = registered_table("track_sources", consumer="Catalog cleanup")
        self._track_artists = registered_table(
            "track_artists", consumer="Catalog cleanup"
        )
        self._source_artists = registered_table(
            "track_source_artists", consumer="Catalog cleanup"
        )
        self._discovery_results = registered_table(
            "discovery_results", consumer="Catalog cleanup"
        )
        self._requests = registered_table("track_requests", consumer="Catalog cleanup")
        self._radio_runs = registered_table("radio_runs", consumer="Catalog cleanup")
        self._radio_candidates = registered_table(
            "radio_candidates", consumer="Catalog cleanup"
        )
        self._radio_exclusions = registered_table(
            "radio_exclusions", consumer="Catalog cleanup"
        )
        self._reactions = registered_table(
            "track_reactions", consumer="Catalog cleanup"
        )
        self._playlist_entries = registered_table(
            "playlist_entries", consumer="Catalog cleanup"
        )

    async def candidates(
        self,
        session: AsyncSession,
        *,
        checked_before: datetime,
        limit: int,
    ) -> CatalogCleanupCandidates:
        """Lock and return the exact rows a cleanup execution may remove."""
        if limit < 1:
            raise ValueError("Catalog cleanup limit must be positive.")

        source_rows = (
            (
                await session.execute(
                    select(
                        self._sources.c.id,
                        self._sources.c.track_id,
                        self._sources.c.provider,
                        self._sources.c.external_id,
                        self._sources.c.checked_at,
                        self._tracks.c.title,
                    )
                    .select_from(
                        self._sources.join(
                            self._tracks,
                            self._tracks.c.id == self._sources.c.track_id,
                        )
                    )
                    .where(
                        self._sources.c.checked_at < checked_before,
                        ~exists().where(
                            self._discovery_results.c.track_source_id
                            == self._sources.c.id
                        ),
                        ~exists().where(
                            or_(
                                self._requests.c.source_id == self._sources.c.id,
                                self._requests.c.track_id == self._sources.c.track_id,
                            )
                        ),
                        ~exists().where(
                            self._radio_runs.c.seed_track_source_id
                            == self._sources.c.id
                        ),
                        ~exists().where(
                            or_(
                                self._radio_candidates.c.source_id
                                == self._sources.c.id,
                                self._radio_candidates.c.track_id
                                == self._sources.c.track_id,
                            )
                        ),
                        ~exists().where(
                            self._radio_exclusions.c.track_id
                            == self._sources.c.track_id
                        ),
                        ~exists().where(
                            self._reactions.c.track_id == self._sources.c.track_id
                        ),
                        ~exists().where(
                            or_(
                                self._playlist_entries.c.preferred_source_id
                                == self._sources.c.id,
                                (
                                    self._playlist_entries.c.track_id
                                    == self._sources.c.track_id
                                )
                                & self._playlist_entries.c.preferred_source_id.is_(
                                    None
                                ),
                            )
                        ),
                    )
                    .order_by(self._sources.c.checked_at, self._sources.c.id)
                    .limit(limit)
                    .with_for_update(of=(self._sources, self._tracks))
                )
            )
            .mappings()
            .all()
        )
        sources = tuple(self._source(row) for row in source_rows)
        if not sources:
            return CatalogCleanupCandidates((), (), ())

        source_ids = tuple(source.id for source in sources)
        possible_track_ids = tuple(dict.fromkeys(source.track_id for source in sources))
        track_rows = (
            (
                await session.execute(
                    select(self._tracks.c.id, self._tracks.c.title)
                    .where(
                        self._tracks.c.id.in_(possible_track_ids),
                        ~exists().where(
                            self._sources.c.track_id == self._tracks.c.id,
                            self._sources.c.id.not_in(source_ids),
                        ),
                    )
                    .order_by(self._tracks.c.id)
                    .with_for_update(of=self._tracks)
                )
            )
            .mappings()
            .all()
        )
        tracks = tuple(
            CatalogCleanupTrack(TrackId(row["id"]), cast(str, row["title"]))
            for row in track_rows
        )
        track_ids = tuple(track.id for track in tracks)

        impacted_artists = union(
            select(self._source_artists.c.artist_id).where(
                self._source_artists.c.track_source_id.in_(source_ids)
            ),
            select(self._track_artists.c.artist_id).where(
                self._track_artists.c.track_id.in_(track_ids)
            ),
        ).subquery()
        artist_rows = (
            (
                await session.execute(
                    select(self._artists.c.id, self._artists.c.name)
                    .where(
                        self._artists.c.id.in_(select(impacted_artists.c.artist_id)),
                        ~exists().where(
                            self._source_artists.c.artist_id == self._artists.c.id,
                            self._source_artists.c.track_source_id.not_in(source_ids),
                        ),
                        ~exists().where(
                            self._track_artists.c.artist_id == self._artists.c.id,
                            self._track_artists.c.track_id.not_in(track_ids),
                        ),
                    )
                    .order_by(self._artists.c.id)
                    .with_for_update(of=self._artists)
                )
            )
            .mappings()
            .all()
        )
        artists = tuple(
            CatalogCleanupArtist(ArtistId(row["id"]), cast(str, row["name"]))
            for row in artist_rows
        )
        return CatalogCleanupCandidates(sources, tracks, artists)

    @staticmethod
    def _source(row: Any) -> CatalogCleanupSource:
        return CatalogCleanupSource(
            TrackSourceId(row["id"]),
            TrackId(row["track_id"]),
            cast(str, row["title"]),
            ProviderName(row["provider"]),
            cast(str, row["external_id"]),
            row["checked_at"],
        )

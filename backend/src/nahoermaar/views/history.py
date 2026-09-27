# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queryable read-only projection of confirmed shared playback."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, and_, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import ProviderName, TrackId, TrackSourceId
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.listening.domain import PlaybackEndReason, PlaybackRecordId
from nahoermaar.player.domain import RadioRunId, RequestOrigin, TrackRequestId
from nahoermaar.users.domain import UserId

type UnitFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class PlaybackHistorySnapshot:
    started_at: datetime
    playback_id: PlaybackRecordId


@dataclass(frozen=True, slots=True)
class PlaybackHistoryEntry:
    playback_id: PlaybackRecordId
    request_id: TrackRequestId
    track_id: TrackId
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    origin: RequestOrigin
    requested_by: UserId
    radio_run_id: RadioRunId | None
    source_id: TrackSourceId | None
    source_url: str | None
    source_provider: ProviderName | None
    contributor_display_name: str
    contributor_discord_id: str | None
    contributor_discord_username: str | None
    contributor_discord_avatar_hash: str | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: PlaybackEndReason | None
    audio_seconds: float
    group_audio_seconds: float


@dataclass(frozen=True, slots=True)
class PlaybackHistoryContributor:
    user_id: UserId
    display_name: str
    discord_id: str | None
    discord_avatar_hash: str | None


@dataclass(frozen=True, slots=True)
class PlaybackHistoryPage:
    entries: tuple[PlaybackHistoryEntry, ...]
    contributors: tuple[PlaybackHistoryContributor, ...]
    page: int
    page_size: int
    total: int
    page_count: int
    snapshot: PlaybackHistorySnapshot | None


class PlaybackHistoryView:
    """Read stable numbered pages of every confirmed playback start."""

    __slots__ = (
        "_artists",
        "_discord",
        "_playbacks",
        "_profiles",
        "_requests",
        "_sources",
        "_track_artists",
        "_tracks",
        "_units",
    )

    def __init__(self, units: UnitFactory) -> None:
        self._units = units
        self._playbacks = _table("playback_records")
        self._requests = _table("track_requests")
        self._tracks = _table("tracks")
        self._sources = _table("track_sources")
        self._track_artists = _table("track_artists")
        self._artists = _table("artists")
        self._profiles = _table("user_profiles")
        self._discord = _table("discord_identities")

    async def get(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        query: str | None = None,
        radio: bool | None = None,
        requested_by: UserId | None = None,
        snapshot: PlaybackHistorySnapshot | None = None,
    ) -> PlaybackHistoryPage:
        if page < 1:
            raise ValueError("Playback history page must be positive.")
        if not 1 <= page_size <= 100:
            raise ValueError("Playback history page size must be between 1 and 100.")

        contributor_id = self._requests.c.requested_by
        relation = (
            self._playbacks.join(
                self._requests,
                self._requests.c.id == self._playbacks.c.request_id,
            )
            .join(
                self._tracks,
                self._tracks.c.id == self._requests.c.track_id,
            )
            .outerjoin(
                self._sources,
                self._sources.c.id == self._requests.c.source_id,
            )
            .outerjoin(
                self._profiles,
                self._profiles.c.user_id == contributor_id,
            )
            .outerjoin(
                self._discord,
                self._discord.c.user_id == contributor_id,
            )
        )
        artist_names = (
            select(
                func.array_agg(
                    aggregate_order_by(
                        self._artists.c.name,
                        self._track_artists.c.position,
                    )
                )
            )
            .select_from(
                self._track_artists.join(
                    self._artists,
                    self._artists.c.id == self._track_artists.c.artist_id,
                )
            )
            .where(self._track_artists.c.track_id == self._tracks.c.id)
            .scalar_subquery()
        )
        filters = list(self._search_filters(query))
        if radio is not None:
            radio_filter = self._requests.c.origin == RequestOrigin.RADIO.value
            filters.append(radio_filter if radio else ~radio_filter)
        if requested_by is not None:
            filters.append(self._requests.c.requested_by == requested_by)

        async with self._units() as work:
            if snapshot is None:
                newest = (
                    await work.session.execute(
                        select(
                            self._playbacks.c.started_at,
                            self._playbacks.c.id,
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._playbacks.c.started_at.desc(),
                            self._playbacks.c.id.desc(),
                        )
                        .limit(1)
                    )
                ).first()
                if newest is not None:
                    snapshot = PlaybackHistorySnapshot(
                        newest.started_at,
                        PlaybackRecordId(newest.id),
                    )

            if snapshot is not None:
                filters.append(
                    or_(
                        self._playbacks.c.started_at < snapshot.started_at,
                        and_(
                            self._playbacks.c.started_at == snapshot.started_at,
                            self._playbacks.c.id <= snapshot.playback_id,
                        ),
                    )
                )

            total = int(
                await work.session.scalar(
                    select(func.count()).select_from(relation).where(*filters)
                )
                or 0
            )
            rows = (
                (
                    await work.session.execute(
                        select(
                            self._playbacks.c.id.label("playback_id"),
                            self._playbacks.c.request_id,
                            self._tracks.c.id.label("track_id"),
                            self._tracks.c.title,
                            self._tracks.c.artwork_url,
                            self._tracks.c.duration_seconds,
                            self._requests.c.origin,
                            self._requests.c.requested_by,
                            self._requests.c.radio_run_id,
                            self._sources.c.id.label("source_id"),
                            self._sources.c.source_url,
                            self._sources.c.provider.label("source_provider"),
                            self._profiles.c.display_name,
                            self._discord.c.discord_id,
                            self._discord.c.username.label("discord_username"),
                            self._discord.c.avatar_hash.label("discord_avatar_hash"),
                            self._playbacks.c.started_at,
                            self._playbacks.c.ended_at,
                            self._playbacks.c.end_reason,
                            self._playbacks.c.audio_seconds,
                            self._playbacks.c.group_audio_seconds,
                            artist_names.label("artist_names"),
                        )
                        .select_from(relation)
                        .where(*filters)
                        .order_by(
                            self._playbacks.c.started_at.desc(),
                            self._playbacks.c.id.desc(),
                        )
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .mappings()
                .all()
            )
            contributor_rows = (
                (
                    await work.session.execute(
                        select(
                            self._requests.c.requested_by,
                            self._profiles.c.display_name,
                            self._discord.c.username.label("discord_username"),
                            self._discord.c.discord_id,
                            self._discord.c.avatar_hash.label("discord_avatar_hash"),
                        )
                        .select_from(relation)
                        .distinct()
                        .order_by(
                            self._profiles.c.display_name,
                            self._discord.c.username,
                            self._requests.c.requested_by,
                        )
                    )
                )
                .mappings()
                .all()
            )

        entries = tuple(self._entry(row) for row in rows)
        contributors = tuple(self._contributor(row) for row in contributor_rows)
        return PlaybackHistoryPage(
            entries,
            contributors,
            page,
            page_size,
            total,
            (total + page_size - 1) // page_size,
            snapshot,
        )

    def _contributor(self, row: Any) -> PlaybackHistoryContributor:
        user_id = UserId(row["requested_by"])
        username = cast(str | None, row["discord_username"])
        display_name = cast(str | None, row["display_name"])
        return PlaybackHistoryContributor(
            user_id,
            display_name or username or f"Listener {str(user_id)[:8]}",
            row["discord_id"],
            row["discord_avatar_hash"],
        )

    def _search_filters(self, query: str | None) -> tuple[Any, ...]:
        normalized = query.strip() if query is not None else ""
        if not normalized:
            return ()
        pattern = f"%{normalized}%"
        matching_artist = (
            select(1)
            .select_from(
                self._track_artists.join(
                    self._artists,
                    self._artists.c.id == self._track_artists.c.artist_id,
                )
            )
            .where(
                self._track_artists.c.track_id == self._tracks.c.id,
                self._artists.c.name.ilike(pattern),
            )
            .exists()
        )
        return (or_(self._tracks.c.title.ilike(pattern), matching_artist),)

    def _entry(self, row: Any) -> PlaybackHistoryEntry:
        contributor = UserId(row["requested_by"])
        username = cast(str | None, row["discord_username"])
        display_name = cast(str | None, row["display_name"])
        return PlaybackHistoryEntry(
            PlaybackRecordId(row["playback_id"]),
            TrackRequestId(row["request_id"]),
            TrackId(row["track_id"]),
            row["title"],
            tuple(cast(list[str] | None, row["artist_names"]) or ()),
            row["artwork_url"],
            row["duration_seconds"],
            RequestOrigin(row["origin"]),
            UserId(row["requested_by"]),
            RadioRunId(row["radio_run_id"])
            if row["radio_run_id"] is not None
            else None,
            TrackSourceId(row["source_id"]) if row["source_id"] is not None else None,
            row["source_url"],
            ProviderName(row["source_provider"])
            if row["source_provider"] is not None
            else None,
            display_name or username or f"Listener {str(contributor)[:8]}",
            row["discord_id"],
            username,
            row["discord_avatar_hash"],
            row["started_at"],
            row["ended_at"],
            PlaybackEndReason(row["end_reason"])
            if row["end_reason"] is not None
            else None,
            float(row["audio_seconds"]),
            float(row["group_audio_seconds"]),
        )


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(
            f"Playback-history table is not registered: {name}"
        ) from error

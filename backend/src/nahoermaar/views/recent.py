# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Read-only projection of recent shared playback."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import ProviderName, TrackId, TrackSourceId
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.listening.domain import PlaybackEndReason, PlaybackRecordId
from nahoermaar.player.domain import RequestOrigin, TrackRequestId
from nahoermaar.users.domain import UserId

type UnitFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class RecentPlayback:
    playback_id: PlaybackRecordId
    request_id: TrackRequestId
    track_id: TrackId
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    origin: RequestOrigin
    requested_by: UserId | None
    source_id: TrackSourceId | None
    source_url: str | None
    source_provider: ProviderName | None
    contributor_id: UserId | None
    contributor_display_name: str | None
    contributor_pixabot: str | None
    contributor_discord_id: str | None
    contributor_discord_username: str | None
    contributor_discord_avatar_hash: str | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: PlaybackEndReason | None
    audio_seconds: float
    group_audio_seconds: float
    play_count: int


class RecentListeningView:
    """Read the latest 100 starts and collapse repeated tracks for display."""

    __slots__ = (
        "_artists",
        "_discord",
        "_playbacks",
        "_profiles",
        "_radio_runs",
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
        self._radio_runs = _table("radio_runs")
        self._profiles = _table("user_profiles")
        self._discord = _table("discord_identities")

    async def get(self, *, limit: int = 20) -> tuple[RecentPlayback, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Recent playback limit must be between 1 and 100.")
        contributor_id = func.coalesce(
            self._requests.c.requested_by,
            self._radio_runs.c.initiated_by,
        )
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
                self._radio_runs,
                self._radio_runs.c.id == self._requests.c.radio_run_id,
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
        artists = (
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
        async with self._units() as work:
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
                            self._sources.c.id.label("source_id"),
                            self._sources.c.source_url,
                            self._sources.c.provider.label("source_provider"),
                            contributor_id.label("contributor_id"),
                            self._profiles.c.display_name,
                            self._profiles.c.pixabot,
                            self._discord.c.discord_id,
                            self._discord.c.username.label("discord_username"),
                            self._discord.c.avatar_hash.label("discord_avatar_hash"),
                            self._playbacks.c.started_at,
                            self._playbacks.c.ended_at,
                            self._playbacks.c.end_reason,
                            self._playbacks.c.audio_seconds,
                            self._playbacks.c.group_audio_seconds,
                            artists.label("artist_names"),
                        )
                        .select_from(relation)
                        .order_by(
                            self._playbacks.c.started_at.desc(),
                            self._playbacks.c.id,
                        )
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )

        grouped: dict[TrackId, RecentPlayback] = {}
        ordered: list[TrackId] = []
        for row in rows:
            track_id = TrackId(row["track_id"])
            existing = grouped.get(track_id)
            if existing is not None:
                grouped[track_id] = replace(
                    existing,
                    play_count=existing.play_count + 1,
                )
                continue
            ordered.append(track_id)
            contributor = (
                UserId(row["contributor_id"])
                if row["contributor_id"] is not None
                else None
            )
            username = cast(str | None, row["discord_username"])
            display_name = cast(str | None, row["display_name"])
            grouped[track_id] = RecentPlayback(
                PlaybackRecordId(row["playback_id"]),
                TrackRequestId(row["request_id"]),
                track_id,
                row["title"],
                tuple(cast(list[str] | None, row["artist_names"]) or ()),
                row["artwork_url"],
                row["duration_seconds"],
                RequestOrigin(row["origin"]),
                UserId(row["requested_by"])
                if row["requested_by"] is not None
                else None,
                TrackSourceId(row["source_id"])
                if row["source_id"] is not None
                else None,
                row["source_url"],
                ProviderName(row["source_provider"])
                if row["source_provider"] is not None
                else None,
                contributor,
                display_name
                or username
                or (f"Listener {str(contributor)[:8]}" if contributor else None),
                row["pixabot"],
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
                1,
            )
        return tuple(grouped[track_id] for track_id in ordered[:limit])


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(
            f"Recent-listening table is not registered: {name}"
        ) from error

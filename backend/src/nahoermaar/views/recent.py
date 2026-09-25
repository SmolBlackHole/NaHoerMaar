# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Read-only projection of recently completed shared playback."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import TrackId
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
    started_at: datetime
    ended_at: datetime
    end_reason: PlaybackEndReason
    audio_seconds: float
    group_audio_seconds: float


class RecentListeningView:
    """Read completed plays without hydrating player or catalog aggregates."""

    __slots__ = (
        "_artists",
        "_playbacks",
        "_requests",
        "_track_artists",
        "_tracks",
        "_units",
    )

    def __init__(self, units: UnitFactory) -> None:
        self._units = units
        self._playbacks = _table("playback_records")
        self._requests = _table("track_requests")
        self._tracks = _table("tracks")
        self._track_artists = _table("track_artists")
        self._artists = _table("artists")

    async def get(self, *, limit: int = 20) -> tuple[RecentPlayback, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("Recent playback limit must be between 1 and 100.")
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
                self._track_artists,
                self._track_artists.c.track_id == self._tracks.c.id,
            )
            .outerjoin(
                self._artists,
                self._artists.c.id == self._track_artists.c.artist_id,
            )
        )
        artists = func.array_agg(
            aggregate_order_by(
                self._artists.c.name,
                self._track_artists.c.position,
            )
        ).filter(self._artists.c.id.is_not(None))
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
                            self._playbacks.c.started_at,
                            self._playbacks.c.ended_at,
                            self._playbacks.c.end_reason,
                            self._playbacks.c.audio_seconds,
                            self._playbacks.c.group_audio_seconds,
                            artists.label("artist_names"),
                        )
                        .select_from(relation)
                        .where(self._playbacks.c.ended_at.is_not(None))
                        .group_by(
                            self._playbacks.c.id,
                            self._requests.c.id,
                            self._tracks.c.id,
                        )
                        .order_by(
                            self._playbacks.c.ended_at.desc(),
                            self._playbacks.c.id,
                        )
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            RecentPlayback(
                PlaybackRecordId(row["playback_id"]),
                TrackRequestId(row["request_id"]),
                TrackId(row["track_id"]),
                row["title"],
                tuple(cast(list[str] | None, row["artist_names"]) or ()),
                row["artwork_url"],
                row["duration_seconds"],
                RequestOrigin(row["origin"]),
                UserId(row["requested_by"])
                if row["requested_by"] is not None
                else None,
                row["started_at"],
                row["ended_at"],
                PlaybackEndReason(row["end_reason"]),
                float(row["audio_seconds"]),
                float(row["group_audio_seconds"]),
            )
            for row in rows
        )


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(
            f"Recent-listening table is not registered: {name}"
        ) from error

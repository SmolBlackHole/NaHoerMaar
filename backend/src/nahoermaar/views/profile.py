# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Combined user, listening and statistics projection for profile pages."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from time import perf_counter
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.listening.domain import PlaybackEndReason, PlaybackRecordId
from nahoermaar.statistics.service import (
    StatisticsPeriod,
    StatisticsReport,
    StatisticsService,
)
from nahoermaar.users.domain import (
    AccessRole,
    Appearance,
    AppearanceMode,
    AuthError,
    AuthErrorCode,
    DiscordIdentity,
    FontFamily,
    IconSet,
    NeutralColor,
    PrimaryColor,
    TextSize,
    UserId,
    UserProfile,
)

type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)
_RECENT_TRACK_LIMIT = 10


@dataclass(frozen=True, slots=True)
class ProfileIdentity:
    user_id: UserId
    discord: DiscordIdentity
    profile: UserProfile
    appearance: Appearance
    role: AccessRole
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecentTrack:
    playback_id: PlaybackRecordId
    track_id: TrackId
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    started_at: datetime
    last_heard_at: datetime
    audio_seconds: float
    end_reason: PlaybackEndReason | None


@dataclass(frozen=True, slots=True)
class ProfileReport:
    identity: ProfileIdentity
    statistics: StatisticsReport
    recent_tracks: tuple[RecentTrack, ...]


class ProfileView:
    """Answer the complete profile read without hydrating feature aggregates."""

    __slots__ = (
        "_artists",
        "_discord",
        "_listeners",
        "_playbacks",
        "_preferences",
        "_profiles",
        "_requests",
        "_statistics",
        "_track_artists",
        "_tracks",
        "_units",
        "_users",
    )

    def __init__(self, units: UnitFactory, statistics: StatisticsService) -> None:
        self._units = units
        self._statistics = statistics
        self._users = _table("users")
        self._discord = _table("discord_identities")
        self._profiles = _table("user_profiles")
        self._preferences = _table("user_preferences")
        self._listeners = _table("playback_listeners")
        self._playbacks = _table("playback_records")
        self._requests = _table("track_requests")
        self._tracks = _table("tracks")
        self._track_artists = _table("track_artists")
        self._artists = _table("artists")

    async def get(
        self,
        user_id: UserId,
        period: StatisticsPeriod = StatisticsPeriod.DAYS_30,
    ) -> ProfileReport:
        started_at = perf_counter()
        async with self._units() as work:
            identity = await self._identity(work, user_id)
            recent_tracks = await self._recent_tracks(work, user_id)
        statistics = await self._statistics.user(user_id, period)
        _LOGGER.info(
            "profile.projected user_id=%s period=%s recent_tracks=%d partial=%s duration_ms=%.1f",
            user_id,
            period.value,
            len(recent_tracks),
            statistics.coverage.partial,
            (perf_counter() - started_at) * 1000,
        )
        return ProfileReport(identity, statistics, recent_tracks)

    async def _identity(
        self,
        work: UnitOfWork,
        user_id: UserId,
    ) -> ProfileIdentity:
        relation = (
            self._users.join(
                self._discord,
                self._discord.c.user_id == self._users.c.id,
            )
            .join(
                self._profiles,
                self._profiles.c.user_id == self._users.c.id,
            )
            .join(
                self._preferences,
                self._preferences.c.user_id == self._users.c.id,
            )
        )
        row = (
            (
                await work.session.execute(
                    select(
                        self._users.c.id,
                        self._users.c.role,
                        self._users.c.created_at,
                        self._users.c.updated_at,
                        self._users.c.last_login_at,
                        self._discord.c.discord_id,
                        self._discord.c.username,
                        self._discord.c.avatar_hash,
                        self._discord.c.synced_at,
                        self._profiles.c.display_name,
                        self._profiles.c.pixabot,
                        self._preferences.c.mode,
                        self._preferences.c.artwork_colors,
                        self._preferences.c.primary_color,
                        self._preferences.c.neutral_color,
                        self._preferences.c.font_family,
                        self._preferences.c.icon_set,
                        self._preferences.c.text_size,
                    )
                    .select_from(relation)
                    .where(
                        self._users.c.id == user_id,
                        self._users.c.role.is_not(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
        return ProfileIdentity(
            UserId(row["id"]),
            DiscordIdentity(
                row["discord_id"],
                row["username"],
                row["avatar_hash"],
                row["synced_at"],
            ),
            UserProfile(row["display_name"], row["pixabot"]),
            Appearance(
                AppearanceMode(row["mode"]),
                bool(row["artwork_colors"]),
                PrimaryColor(row["primary_color"]),
                NeutralColor(row["neutral_color"]),
                FontFamily(row["font_family"]),
                IconSet(row["icon_set"]),
                TextSize(row["text_size"]),
            ),
            AccessRole(row["role"]),
            row["created_at"],
            row["updated_at"],
            row["last_login_at"],
        )

    async def _recent_tracks(
        self,
        work: UnitOfWork,
        user_id: UserId,
    ) -> tuple[RecentTrack, ...]:
        relation = (
            self._listeners.join(
                self._playbacks,
                self._playbacks.c.id == self._listeners.c.playback_id,
            )
            .join(
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
        rows = (
            (
                await work.session.execute(
                    select(
                        self._playbacks.c.id.label("playback_id"),
                        self._tracks.c.id.label("track_id"),
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        self._tracks.c.duration_seconds,
                        self._playbacks.c.started_at,
                        self._playbacks.c.end_reason,
                        self._listeners.c.last_heard_at,
                        self._listeners.c.audio_seconds,
                        artists.label("artist_names"),
                    )
                    .select_from(relation)
                    .where(self._listeners.c.user_id == user_id)
                    .group_by(
                        self._playbacks.c.id,
                        self._tracks.c.id,
                        self._listeners.c.user_id,
                        self._listeners.c.last_heard_at,
                        self._listeners.c.audio_seconds,
                    )
                    .order_by(
                        self._listeners.c.last_heard_at.desc(),
                        self._playbacks.c.id,
                    )
                    .limit(_RECENT_TRACK_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            RecentTrack(
                PlaybackRecordId(row["playback_id"]),
                TrackId(row["track_id"]),
                row["title"],
                tuple(cast(list[str] | None, row["artist_names"]) or ()),
                row["artwork_url"],
                row["duration_seconds"],
                row["started_at"],
                row["last_heard_at"],
                float(row["audio_seconds"]),
                (
                    PlaybackEndReason(row["end_reason"])
                    if row["end_reason"] is not None
                    else None
                ),
            )
            for row in rows
        )


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as error:
        raise RuntimeError(f"Profile table is not registered: {name}") from error

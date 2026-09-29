# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Combined user, listening and statistics projection for profile pages."""

from dataclasses import dataclass
from datetime import datetime
import logging
from time import perf_counter
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.schema import registered_table
from nahoermaar.database.uow import UnitOfWork, UnitOfWorkFactory
from nahoermaar.listening.domain import PlaybackEndReason, PlaybackRecordId
from nahoermaar.library.domain import (
    PlaylistId,
    PlaylistVisibility,
    ReactionValue,
)
from nahoermaar.statistics.models import (
    PersonalStatisticsReport,
    StatisticsPeriod,
)
from nahoermaar.statistics.service import StatisticsService
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

_LOGGER = logging.getLogger(__name__)
_RECENT_TRACK_LIMIT = 10
_LIBRARY_PREVIEW_LIMIT = 4


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
class ProfileLibraryTrack:
    track_id: TrackId
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    reacted_at: datetime


@dataclass(frozen=True, slots=True)
class ProfilePlaylist:
    playlist_id: PlaylistId
    name: str
    entry_count: int
    artwork_urls: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileLibrary:
    likes_count: int
    dislikes_count: int
    public_playlist_count: int
    liked_tracks: tuple[ProfileLibraryTrack, ...]
    disliked_tracks: tuple[ProfileLibraryTrack, ...]
    public_playlists: tuple[ProfilePlaylist, ...]


@dataclass(frozen=True, slots=True)
class ProfileReport:
    identity: ProfileIdentity
    statistics: PersonalStatisticsReport
    recent_tracks: tuple[RecentTrack, ...]
    library: ProfileLibrary


class ProfileView:
    """Answer the complete profile read without hydrating feature aggregates."""

    __slots__ = (
        "_artists",
        "_collaborators",
        "_discord",
        "_listeners",
        "_playbacks",
        "_playlist_entries",
        "_playlists",
        "_preferences",
        "_profiles",
        "_reactions",
        "_requests",
        "_statistics",
        "_track_artists",
        "_tracks",
        "_units",
        "_users",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        statistics: StatisticsService,
    ) -> None:
        self._units = units
        self._statistics = statistics
        self._users = registered_table("users", consumer="Profile")
        self._discord = registered_table("discord_identities", consumer="Profile")
        self._profiles = registered_table("user_profiles", consumer="Profile")
        self._preferences = registered_table("user_preferences", consumer="Profile")
        self._listeners = registered_table("playback_listeners", consumer="Profile")
        self._playbacks = registered_table("playback_records", consumer="Profile")
        self._requests = registered_table("track_requests", consumer="Profile")
        self._reactions = registered_table("track_reactions", consumer="Profile")
        self._playlists = registered_table("playlists", consumer="Profile")
        self._collaborators = registered_table(
            "playlist_collaborators", consumer="Profile"
        )
        self._playlist_entries = registered_table(
            "playlist_entries", consumer="Profile"
        )
        self._tracks = registered_table("tracks", consumer="Profile")
        self._track_artists = registered_table("track_artists", consumer="Profile")
        self._artists = registered_table("artists", consumer="Profile")

    async def get(
        self,
        user_id: UserId,
        period: StatisticsPeriod = StatisticsPeriod.DAYS_30,
    ) -> ProfileReport:
        started_at = perf_counter()
        async with self._units() as work:
            identity = await self._identity(work, user_id)
            recent_tracks = await self._recent_tracks(work, user_id)
            library = await self._library(work, user_id)
        statistics = await self._statistics.user(user_id, period)
        _LOGGER.info(
            "profile.projected user_id=%s period=%s recent_tracks=%d partial=%s duration_ms=%.1f",
            user_id,
            period.value,
            len(recent_tracks),
            statistics.coverage.partial,
            (perf_counter() - started_at) * 1000,
        )
        return ProfileReport(identity, statistics, recent_tracks, library)

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
            UserProfile(row["display_name"]),
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

    async def _library(
        self,
        work: UnitOfWork,
        user_id: UserId,
    ) -> ProfileLibrary:
        reaction_counts = (
            await work.session.execute(
                select(
                    func.count()
                    .filter(self._reactions.c.value == ReactionValue.LIKE.value)
                    .label("likes"),
                    func.count()
                    .filter(self._reactions.c.value == ReactionValue.DISLIKE.value)
                    .label("dislikes"),
                ).where(self._reactions.c.user_id == user_id)
            )
        ).one()
        public_playlist_count = int(
            await work.session.scalar(
                select(func.count())
                .select_from(self._playlists)
                .where(
                    self._public_playlist_affiliation(user_id),
                    self._playlists.c.visibility == PlaylistVisibility.PUBLIC.value,
                )
            )
            or 0
        )
        liked_tracks = await self._reaction_preview(
            work,
            user_id,
            ReactionValue.LIKE,
        )
        disliked_tracks = await self._reaction_preview(
            work,
            user_id,
            ReactionValue.DISLIKE,
        )
        public_playlists = await self._playlist_preview(work, user_id)
        return ProfileLibrary(
            int(reaction_counts.likes),
            int(reaction_counts.dislikes),
            public_playlist_count,
            liked_tracks,
            disliked_tracks,
            public_playlists,
        )

    async def _reaction_preview(
        self,
        work: UnitOfWork,
        user_id: UserId,
        reaction: ReactionValue,
    ) -> tuple[ProfileLibraryTrack, ...]:
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
        rows = (
            (
                await work.session.execute(
                    select(
                        self._tracks.c.id.label("track_id"),
                        self._tracks.c.title,
                        self._tracks.c.artwork_url,
                        self._tracks.c.duration_seconds,
                        self._reactions.c.updated_at,
                        artist_names.label("artist_names"),
                    )
                    .select_from(
                        self._reactions.join(
                            self._tracks,
                            self._tracks.c.id == self._reactions.c.track_id,
                        )
                    )
                    .where(
                        self._reactions.c.user_id == user_id,
                        self._reactions.c.value == reaction.value,
                    )
                    .order_by(
                        self._reactions.c.updated_at.desc(),
                        self._reactions.c.track_id.desc(),
                    )
                    .limit(_LIBRARY_PREVIEW_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        return tuple(
            ProfileLibraryTrack(
                TrackId(row["track_id"]),
                cast(str, row["title"]),
                tuple(cast(list[str] | None, row["artist_names"]) or ()),
                row["artwork_url"],
                row["duration_seconds"],
                row["updated_at"],
            )
            for row in rows
        )

    async def _playlist_preview(
        self,
        work: UnitOfWork,
        user_id: UserId,
    ) -> tuple[ProfilePlaylist, ...]:
        entry_count = (
            select(func.count())
            .select_from(self._playlist_entries)
            .where(self._playlist_entries.c.playlist_id == self._playlists.c.id)
            .scalar_subquery()
        )
        rows = (
            (
                await work.session.execute(
                    select(
                        self._playlists.c.id.label("playlist_id"),
                        self._playlists.c.name,
                        self._playlists.c.updated_at,
                        entry_count.label("entry_count"),
                    )
                    .where(
                        self._public_playlist_affiliation(user_id),
                        self._playlists.c.visibility == PlaylistVisibility.PUBLIC.value,
                    )
                    .order_by(
                        self._playlists.c.updated_at.desc(),
                        self._playlists.c.id.desc(),
                    )
                    .limit(_LIBRARY_PREVIEW_LIMIT)
                )
            )
            .mappings()
            .all()
        )
        playlist_ids = tuple(PlaylistId(row["playlist_id"]) for row in rows)
        artworks = await self._playlist_artworks(work, playlist_ids)
        return tuple(
            ProfilePlaylist(
                PlaylistId(row["playlist_id"]),
                cast(str, row["name"]),
                int(row["entry_count"]),
                artworks.get(PlaylistId(row["playlist_id"]), ()),
                row["updated_at"],
            )
            for row in rows
        )

    def _public_playlist_affiliation(self, user_id: UserId) -> Any:
        collaborator_playlists = select(self._collaborators.c.playlist_id).where(
            self._collaborators.c.user_id == user_id
        )
        return or_(
            self._playlists.c.owner_id == user_id,
            self._playlists.c.id.in_(collaborator_playlists),
        )

    async def _playlist_artworks(
        self,
        work: UnitOfWork,
        playlist_ids: tuple[PlaylistId, ...],
    ) -> dict[PlaylistId, tuple[str, ...]]:
        if not playlist_ids:
            return {}
        rows = (
            (
                await work.session.execute(
                    select(
                        self._playlist_entries.c.playlist_id,
                        self._tracks.c.artwork_url,
                    )
                    .select_from(
                        self._playlist_entries.join(
                            self._tracks,
                            self._tracks.c.id == self._playlist_entries.c.track_id,
                        )
                    )
                    .where(
                        self._playlist_entries.c.playlist_id.in_(playlist_ids),
                        self._tracks.c.artwork_url.is_not(None),
                    )
                    .order_by(
                        self._playlist_entries.c.playlist_id,
                        self._playlist_entries.c.position,
                    )
                )
            )
            .mappings()
            .all()
        )
        collected: dict[PlaylistId, list[str]] = {}
        for row in rows:
            playlist_id = PlaylistId(row["playlist_id"])
            artwork = cast(str, row["artwork_url"])
            values = collected.setdefault(playlist_id, [])
            if artwork not in values and len(values) < 4:
                values.append(artwork)
        return {playlist_id: tuple(values) for playlist_id, values in collected.items()}

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Profile, appearance and ordinary access administration endpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nahoermaar.bootstrap import Application
from nahoermaar.integrations.avatars import AvatarUnavailableError
from nahoermaar.listening.domain import PlaybackEndReason
from nahoermaar.statistics.models import StatisticsPeriod
from nahoermaar.users.domain import (
    AccessAction,
    AccessEvent,
    AccessRole,
    Appearance,
    AppearanceMode,
    DiscordMember,
    FontFamily,
    IconSet,
    NeutralColor,
    PrimaryColor,
    TextSize,
    User,
    UserId,
    UserProfile,
)
from nahoermaar.users.service import (
    AccessSnapshot,
    GrantAccess,
    RevokeAccess,
    UpdateUser,
)
from nahoermaar.views.profile import ProfileReport

from .middleware import authenticated
from .errors import ApiError, ApiErrorCode, error_responses
from .statistics import PersonalStatisticsView, personal_statistics_view


class DiscordView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str | None
    display_name: str | None
    avatar_url: str | None
    synced_at: datetime | None


class ProfileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None
    complete: bool


class AppearanceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AppearanceMode
    artwork_colors: bool
    primary_color: PrimaryColor
    neutral_color: NeutralColor
    font_family: FontFamily
    icon_set: IconSet
    text_size: TextSize


class UserView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    discord: DiscordView
    profile: ProfileView
    appearance: AppearanceView
    role: AccessRole | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class RecentTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playback_id: UUID
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    started_at: datetime
    last_heard_at: datetime
    audio_seconds: float
    end_reason: PlaybackEndReason | None


class ProfileLibraryTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    reacted_at: datetime


class ProfilePlaylistView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: UUID
    name: str
    entry_count: int
    artwork_urls: tuple[str, ...]
    updated_at: datetime


class ProfileLibraryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    likes_count: int
    dislikes_count: int
    public_playlist_count: int
    liked_tracks: tuple[ProfileLibraryTrackView, ...]
    disliked_tracks: tuple[ProfileLibraryTrackView, ...]
    public_playlists: tuple[ProfilePlaylistView, ...]


class ProfilePageView(UserView):
    statistics: PersonalStatisticsView
    recent_tracks: tuple[RecentTrackView, ...]
    library: ProfileLibraryView


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=32)


class AppearanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AppearanceMode
    artwork_colors: bool
    primary_color: PrimaryColor
    neutral_color: NeutralColor
    font_family: FontFamily
    icon_set: IconSet
    text_size: TextSize


class CurrentUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProfileUpdate | None = None
    appearance: AppearanceUpdate | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.profile is None and self.appearance is None:
            raise ValueError("profile or appearance is required")
        return self


class AccessEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    subject_user_id: UUID
    actor_user_id: UUID | None
    action: AccessAction
    role_before: AccessRole | None
    role_after: AccessRole | None
    occurred_at: datetime


class AccessGrantView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserView
    granted_by_user_id: UUID
    granted_at: datetime


class AccessView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operators: tuple[UserView, ...]
    grants: tuple[AccessGrantView, ...]
    history: tuple[AccessEventView, ...]


class DiscordMemberView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discord_id: str
    username: str
    display_name: str
    avatar_url: str | None
    guild_id: str
    guild_name: str


class DiscordMembersView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    members: tuple[DiscordMemberView, ...]


def router(application: Application) -> APIRouter:
    """Build user and access routes around the composed application."""
    routes = APIRouter(prefix="/api", tags=["users"])

    @routes.get(
        "/users/me",
        operation_id="getCurrentUser",
        responses=error_responses(401, 500, 503),
    )
    async def own_account(request: Request) -> UserView:
        return _user_view(authenticated(request).user, application)

    @routes.patch(
        "/users/me",
        operation_id="updateCurrentUser",
        responses=error_responses(401, 403, 422, 500, 503),
    )
    async def update_current_user(
        request: Request,
        body: CurrentUserUpdate,
    ) -> UserView:
        current = authenticated(request)
        user = await application.bus.execute(
            UpdateUser(
                current.user.id,
                profile=(
                    UserProfile(body.profile.display_name.strip())
                    if body.profile is not None
                    else None
                ),
                appearance=(
                    Appearance(
                        body.appearance.mode,
                        body.appearance.artwork_colors,
                        body.appearance.primary_color,
                        body.appearance.neutral_color,
                        body.appearance.font_family,
                        body.appearance.icon_set,
                        body.appearance.text_size,
                    )
                    if body.appearance is not None
                    else None
                ),
            )
        )
        return _user_view(user, application)

    @routes.get(
        "/profiles/me",
        operation_id="getCurrentProfile",
        responses=error_responses(401, 422, 500, 503),
    )
    async def own_profile(
        request: Request,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> ProfilePageView:
        return _profile_page_view(
            await application.views.profiles.get(
                authenticated(request).user.id,
                period,
            ),
            application,
        )

    @routes.get(
        "/profiles/{user_id}",
        operation_id="getProfile",
        responses=error_responses(401, 404, 422, 500, 503),
    )
    async def profile(
        request: Request,
        user_id: UUID,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> ProfilePageView:
        authenticated(request)
        return _profile_page_view(
            await application.views.profiles.get(UserId(user_id), period),
            application,
        )

    @routes.get(
        "/access",
        operation_id="getAccess",
        responses=error_responses(401, 403, 422, 500, 503),
    )
    async def access_state(
        request: Request,
        history_limit: int = Query(default=100, ge=1, le=500),
    ) -> AccessView:
        await application.users.access.require_admin(authenticated(request).user.id)
        return _access_view(
            await application.users.access.snapshot(history_limit),
            application,
        )

    @routes.get(
        "/access/members",
        operation_id="listDiscordMembers",
        responses=error_responses(401, 403, 422, 500, 503),
    )
    async def discord_members(
        request: Request,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        guild_id: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    ) -> DiscordMembersView:
        await application.users.access.require_admin(authenticated(request).user.id)
        gateway = application.integrations.gateway
        members = gateway.members(query=q, guild_id=guild_id) if gateway else ()
        return DiscordMembersView(
            members=tuple(_member_view(member, application) for member in members)
        )

    @routes.get("/avatars/discord/{discord_id}", include_in_schema=False)
    async def discord_avatar(
        request: Request,
        discord_id: str,
        v: Annotated[str, Query(pattern=r"^[0-9a-f]{16}$")],
    ) -> FileResponse:
        authenticated(request)
        try:
            asset = await application.integrations.avatars.get(discord_id, v)
        except AvatarUnavailableError as error:
            raise ApiError(ApiErrorCode.AVATAR_UNAVAILABLE, 404) from error
        return FileResponse(asset.path, media_type=asset.media_type)

    @routes.put(
        "/access/{discord_id}",
        operation_id="grantAccess",
        responses=error_responses(401, 403, 409, 422, 500, 503),
    )
    async def grant_access(request: Request, discord_id: str) -> AccessEventView | None:
        change = await application.bus.execute(
            GrantAccess(authenticated(request).user.id, discord_id)
        )
        return _event_view(change) if change is not None else None

    @routes.delete(
        "/access/{discord_id}",
        operation_id="revokeAccess",
        responses=error_responses(401, 403, 409, 422, 500, 503),
    )
    async def revoke_access(
        request: Request, discord_id: str
    ) -> AccessEventView | None:
        change = await application.bus.execute(
            RevokeAccess(authenticated(request).user.id, discord_id)
        )
        return _event_view(change) if change is not None else None

    return routes


def _user_view(user: User, application: Application) -> UserView:
    gateway = application.integrations.gateway
    members = gateway.members() if gateway else ()
    member = next(
        (
            candidate
            for candidate in members
            if candidate.discord_id == user.discord.discord_id
        ),
        None,
    )
    return UserView(
        id=user.id,
        discord=DiscordView(
            id=user.discord.discord_id,
            username=user.discord.username or (member.username if member else None),
            display_name=member.display_name if member else None,
            avatar_url=application.integrations.avatars.public_url(
                user.discord.discord_id,
                avatar_hash=user.discord.avatar_hash,
                source_url=member.avatar_url if member else None,
            ),
            synced_at=user.discord.synced_at,
        ),
        profile=ProfileView(
            display_name=user.profile.display_name,
            complete=user.profile_complete,
        ),
        appearance=AppearanceView(
            mode=user.appearance.mode,
            artwork_colors=user.appearance.artwork_colors,
            primary_color=user.appearance.primary_color,
            neutral_color=user.appearance.neutral_color,
            font_family=user.appearance.font_family,
            icon_set=user.appearance.icon_set,
            text_size=user.appearance.text_size,
        ),
        role=user.role,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
    )


def _grant_view(user: User, application: Application) -> AccessGrantView:
    if user.access_granted_by is None or user.access_granted_at is None:
        raise ValueError("Ordinary access grant is missing its actor or timestamp.")
    return AccessGrantView(
        user=_user_view(user, application),
        granted_by_user_id=user.access_granted_by,
        granted_at=user.access_granted_at,
    )


def _access_view(snapshot: AccessSnapshot, application: Application) -> AccessView:
    return AccessView(
        operators=tuple(_user_view(user, application) for user in snapshot.operators),
        grants=tuple(_grant_view(user, application) for user in snapshot.grants),
        history=tuple(_event_view(event) for event in snapshot.history),
    )


def _profile_page_view(
    report: ProfileReport,
    application: Application,
) -> ProfilePageView:
    identity = report.identity
    gateway = application.integrations.gateway
    members = gateway.members() if gateway else ()
    member = next(
        (
            candidate
            for candidate in members
            if candidate.discord_id == identity.discord.discord_id
        ),
        None,
    )
    return ProfilePageView(
        id=identity.user_id,
        discord=DiscordView(
            id=identity.discord.discord_id,
            username=identity.discord.username or (member.username if member else None),
            display_name=member.display_name if member else None,
            avatar_url=application.integrations.avatars.public_url(
                identity.discord.discord_id,
                avatar_hash=identity.discord.avatar_hash,
                source_url=member.avatar_url if member else None,
            ),
            synced_at=identity.discord.synced_at,
        ),
        profile=ProfileView(
            display_name=identity.profile.display_name,
            complete=identity.profile.complete,
        ),
        appearance=AppearanceView(
            mode=identity.appearance.mode,
            artwork_colors=identity.appearance.artwork_colors,
            primary_color=identity.appearance.primary_color,
            neutral_color=identity.appearance.neutral_color,
            font_family=identity.appearance.font_family,
            icon_set=identity.appearance.icon_set,
            text_size=identity.appearance.text_size,
        ),
        role=identity.role,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
        last_login_at=identity.last_login_at,
        statistics=personal_statistics_view(
            report.statistics,
        ),
        recent_tracks=tuple(
            RecentTrackView(
                playback_id=track.playback_id,
                track_id=track.track_id,
                title=track.title,
                artist_names=track.artist_names,
                artwork_url=track.artwork_url,
                duration_seconds=track.duration_seconds,
                started_at=track.started_at,
                last_heard_at=track.last_heard_at,
                audio_seconds=track.audio_seconds,
                end_reason=track.end_reason,
            )
            for track in report.recent_tracks
        ),
        library=ProfileLibraryView(
            likes_count=report.library.likes_count,
            dislikes_count=report.library.dislikes_count,
            public_playlist_count=report.library.public_playlist_count,
            liked_tracks=tuple(
                ProfileLibraryTrackView(
                    track_id=track.track_id,
                    title=track.title,
                    artist_names=track.artist_names,
                    artwork_url=track.artwork_url,
                    duration_seconds=track.duration_seconds,
                    reacted_at=track.reacted_at,
                )
                for track in report.library.liked_tracks
            ),
            disliked_tracks=tuple(
                ProfileLibraryTrackView(
                    track_id=track.track_id,
                    title=track.title,
                    artist_names=track.artist_names,
                    artwork_url=track.artwork_url,
                    duration_seconds=track.duration_seconds,
                    reacted_at=track.reacted_at,
                )
                for track in report.library.disliked_tracks
            ),
            public_playlists=tuple(
                ProfilePlaylistView(
                    playlist_id=playlist.playlist_id,
                    name=playlist.name,
                    entry_count=playlist.entry_count,
                    artwork_urls=playlist.artwork_urls,
                    updated_at=playlist.updated_at,
                )
                for playlist in report.library.public_playlists
            ),
        ),
    )


def _event_view(event: AccessEvent) -> AccessEventView:
    return AccessEventView(
        id=event.id,
        subject_user_id=event.subject_user_id,
        actor_user_id=event.actor_user_id,
        action=event.action,
        role_before=event.role_before,
        role_after=event.role_after,
        occurred_at=event.occurred_at,
    )


def _member_view(
    member: DiscordMember,
    application: Application,
) -> DiscordMemberView:
    return DiscordMemberView(
        discord_id=member.discord_id,
        username=member.username,
        display_name=member.display_name,
        avatar_url=application.integrations.avatars.public_url(
            member.discord_id,
            source_url=member.avatar_url,
        ),
        guild_id=member.guild_id,
        guild_name=member.guild_name,
    )

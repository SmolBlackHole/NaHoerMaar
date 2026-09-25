# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Profile, appearance and ordinary access administration endpoints."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.listening.domain import PlaybackEndReason
from nahoermaar.statistics.service import StatisticsPeriod
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
    GrantAccess,
    RevokeAccess,
    SaveAppearance,
    SaveProfile,
)
from nahoermaar.views.profile import ProfileReport

from .middleware import authenticated
from .statistics import StatisticsView, statistics_view


class DiscordView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str | None
    avatar_hash: str | None
    synced_at: datetime | None


class ProfileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None
    pixabot: str | None
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


class ProfilePageView(UserView):
    statistics: StatisticsView
    recent_tracks: tuple[RecentTrackView, ...]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=32)
    pixabot: str = Field(pattern=r"^[0-9a-f]{4}$")


class AppearanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AppearanceMode
    artwork_colors: bool
    primary_color: PrimaryColor
    neutral_color: NeutralColor
    font_family: FontFamily
    icon_set: IconSet
    text_size: TextSize


class AccessEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    subject_user_id: UUID
    actor_user_id: UUID | None
    action: AccessAction
    role_before: AccessRole | None
    role_after: AccessRole | None
    occurred_at: datetime


class AccessView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str
    admin_ids: tuple[str, ...]
    grants: tuple[UserView, ...]
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

    @routes.get("/users/me")
    async def own_profile(
        request: Request,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> ProfilePageView:
        return _profile_page_view(
            await application.profiles.get(authenticated(request).user.id, period)
        )

    @routes.put("/users/me/profile")
    async def update_profile(
        request: Request,
        body: ProfileUpdate,
    ) -> ProfilePageView:
        current = authenticated(request)
        await application.bus.execute(
            SaveProfile(
                current.user.id,
                UserProfile(body.display_name.strip(), body.pixabot),
            )
        )
        return _profile_page_view(await application.profiles.get(current.user.id))

    @routes.put("/users/me/appearance")
    async def update_appearance(
        request: Request,
        body: AppearanceUpdate,
    ) -> ProfilePageView:
        current = authenticated(request)
        await application.bus.execute(
            SaveAppearance(
                current.user.id,
                Appearance(
                    body.mode,
                    body.artwork_colors,
                    body.primary_color,
                    body.neutral_color,
                    body.font_family,
                    body.icon_set,
                    body.text_size,
                ),
            )
        )
        return _profile_page_view(await application.profiles.get(current.user.id))

    @routes.get("/users/{user_id}")
    async def profile(
        request: Request,
        user_id: UUID,
        period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.DAYS_30,
    ) -> ProfilePageView:
        authenticated(request)
        return _profile_page_view(
            await application.profiles.get(UserId(user_id), period)
        )

    @routes.get("/access")
    async def access_state(
        request: Request,
        history_limit: int = Query(default=100, ge=1, le=500),
    ) -> AccessView:
        await application.access.require_admin(authenticated(request).user.id)
        operators = application.access.operators
        return AccessView(
            owner_id=operators.owner_id,
            admin_ids=operators.admin_ids,
            grants=tuple(
                _user_view(user) for user in await application.access.grants()
            ),
            history=tuple(
                _event_view(event)
                for event in await application.access.history(history_limit)
            ),
        )

    @routes.get("/access/members")
    async def discord_members(request: Request) -> DiscordMembersView:
        await application.access.require_admin(authenticated(request).user.id)
        members = application.gateway.members() if application.gateway else ()
        return DiscordMembersView(
            members=tuple(_member_view(member) for member in members)
        )

    @routes.put("/access/{discord_id}")
    async def grant_access(request: Request, discord_id: str) -> AccessEventView | None:
        change = await application.bus.execute(
            GrantAccess(authenticated(request).user.id, discord_id)
        )
        return _event_view(change) if change is not None else None

    @routes.delete("/access/{discord_id}")
    async def revoke_access(
        request: Request, discord_id: str
    ) -> AccessEventView | None:
        change = await application.bus.execute(
            RevokeAccess(authenticated(request).user.id, discord_id)
        )
        return _event_view(change) if change is not None else None

    return routes


def _user_view(user: User) -> UserView:
    return UserView(
        id=user.id,
        discord=DiscordView(
            id=user.discord.discord_id,
            username=user.discord.username,
            avatar_hash=user.discord.avatar_hash,
            synced_at=user.discord.synced_at,
        ),
        profile=ProfileView(
            display_name=user.profile.display_name,
            pixabot=user.profile.pixabot,
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


def _profile_page_view(report: ProfileReport) -> ProfilePageView:
    identity = report.identity
    return ProfilePageView(
        id=identity.user_id,
        discord=DiscordView(
            id=identity.discord.discord_id,
            username=identity.discord.username,
            avatar_hash=identity.discord.avatar_hash,
            synced_at=identity.discord.synced_at,
        ),
        profile=ProfileView(
            display_name=identity.profile.display_name,
            pixabot=identity.profile.pixabot,
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
        statistics=statistics_view(report.statistics),
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


def _member_view(member: DiscordMember) -> DiscordMemberView:
    return DiscordMemberView(
        discord_id=member.discord_id,
        username=member.username,
        display_name=member.display_name,
        avatar_url=member.avatar_url,
        guild_id=member.guild_id,
        guild_name=member.guild_name,
    )

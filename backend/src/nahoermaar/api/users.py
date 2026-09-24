# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Profile, appearance and ordinary access administration endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.users.domain import (
    AccessAction,
    AccessEvent,
    AccessRole,
    Appearance,
    AppearanceMode,
    FontFamily,
    IconSet,
    NeutralColor,
    PrimaryColor,
    TextSize,
    User,
    UserProfile,
)
from nahoermaar.users.service import (
    GrantAccess,
    RevokeAccess,
    SaveAppearance,
    SaveProfile,
)

from .middleware import authenticated


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


def router(application: Application) -> APIRouter:
    """Build user and access routes around the composed application."""
    routes = APIRouter(prefix="/api", tags=["users"])

    @routes.get("/users/me")
    async def own_profile(request: Request) -> UserView:
        return _user_view(
            await application.auth.profile(authenticated(request).user.id)
        )

    @routes.put("/users/me/profile")
    async def update_profile(request: Request, body: ProfileUpdate) -> UserView:
        current = authenticated(request)
        user = await application.bus.execute(
            SaveProfile(
                current.user.id,
                UserProfile(body.display_name.strip(), body.pixabot),
            )
        )
        return _user_view(user)

    @routes.put("/users/me/appearance")
    async def update_appearance(request: Request, body: AppearanceUpdate) -> UserView:
        current = authenticated(request)
        user = await application.bus.execute(
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
        return _user_view(user)

    @routes.get("/users/{discord_id}")
    async def profile(discord_id: str) -> UserView:
        return _user_view(await application.auth.profile_by_discord_id(discord_id))

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

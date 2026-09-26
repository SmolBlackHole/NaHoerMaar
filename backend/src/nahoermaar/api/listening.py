# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated read endpoints for shared listening history."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from nahoermaar.views.recent import RecentListeningView


class RecentContributorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str
    pixabot: str | None
    discord_id: str
    discord_username: str | None
    discord_avatar_hash: str | None


class RecentPlaybackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playback_id: UUID
    request_id: UUID
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    origin: str
    requested_by: UUID | None
    source_id: UUID | None
    source_url: str | None
    source_provider: str | None
    contributor: RecentContributorView | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    audio_seconds: float
    group_audio_seconds: float
    play_count: int


def router(recent: RecentListeningView) -> APIRouter:
    routes = APIRouter(prefix="/api/listening", tags=["listening"])

    @routes.get("/recent")
    async def recent_playback(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> tuple[RecentPlaybackView, ...]:
        return tuple(
            RecentPlaybackView(
                playback_id=item.playback_id,
                request_id=item.request_id,
                track_id=item.track_id,
                title=item.title,
                artist_names=item.artist_names,
                artwork_url=item.artwork_url,
                duration_seconds=item.duration_seconds,
                origin=item.origin.value,
                requested_by=item.requested_by,
                source_id=item.source_id,
                source_url=item.source_url,
                source_provider=(
                    item.source_provider.value
                    if item.source_provider is not None
                    else None
                ),
                contributor=(
                    RecentContributorView(
                        user_id=item.contributor_id,
                        display_name=item.contributor_display_name,
                        pixabot=item.contributor_pixabot,
                        discord_id=item.contributor_discord_id,
                        discord_username=item.contributor_discord_username,
                        discord_avatar_hash=item.contributor_discord_avatar_hash,
                    )
                    if item.contributor_id is not None
                    and item.contributor_display_name is not None
                    and item.contributor_discord_id is not None
                    else None
                ),
                started_at=item.started_at,
                ended_at=item.ended_at,
                end_reason=(
                    item.end_reason.value if item.end_reason is not None else None
                ),
                audio_seconds=item.audio_seconds,
                group_audio_seconds=item.group_audio_seconds,
                play_count=item.play_count,
            )
            for item in await recent.get(limit=limit)
        )

    return routes

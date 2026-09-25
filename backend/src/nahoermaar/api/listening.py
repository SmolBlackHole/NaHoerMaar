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
    started_at: datetime
    ended_at: datetime
    end_reason: str
    audio_seconds: float
    group_audio_seconds: float


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
                started_at=item.started_at,
                ended_at=item.ended_at,
                end_reason=item.end_reason.value,
                audio_seconds=item.audio_seconds,
                group_audio_seconds=item.group_audio_seconds,
            )
            for item in await recent.get(limit=limit)
        )

    return routes

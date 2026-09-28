# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated query endpoint for shared listening history."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.listening.domain import PlaybackRecordId
from nahoermaar.users.domain import UserId
from nahoermaar.views.history import PlaybackHistoryEntry, PlaybackHistorySnapshot

from .pagination import TimestampPosition, decode_position, encode_position


class PlaybackContributorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str
    discord_id: str
    discord_username: str | None
    avatar_url: str


class PlaybackHistoryEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playback_id: UUID
    request_id: UUID
    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    artwork_url: str | None
    duration_seconds: float | None
    origin: str
    requested_by: UUID
    radio_run_id: UUID | None
    source_id: UUID | None
    source_url: str | None
    source_provider: str | None
    contributor: PlaybackContributorView | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    audio_seconds: float
    group_audio_seconds: float


class PlaybackHistoryFilterContributorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str
    avatar_url: str | None


class PlaybackHistoryPageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: tuple[PlaybackHistoryEntryView, ...]
    contributors: tuple[PlaybackHistoryFilterContributorView, ...]
    page: int
    page_size: int
    total: int
    page_count: int
    snapshot: str | None


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/listening", tags=["listening"])

    @routes.get("/recent")
    async def playback_history(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        radio: bool | None = None,
        requested_by: UUID | None = None,
        snapshot: str | None = None,
    ) -> PlaybackHistoryPageView:
        result = await application.views.history.get(
            page=page,
            page_size=page_size,
            query=q,
            radio=radio,
            requested_by=UserId(requested_by) if requested_by is not None else None,
            snapshot=_snapshot(snapshot),
        )
        return PlaybackHistoryPageView(
            entries=tuple(_entry_view(item, application) for item in result.entries),
            contributors=tuple(
                PlaybackHistoryFilterContributorView(
                    user_id=contributor.user_id,
                    display_name=contributor.display_name,
                    avatar_url=(
                        application.integrations.avatars.public_url(
                            contributor.discord_id,
                            avatar_hash=contributor.discord_avatar_hash,
                        )
                        if contributor.discord_id is not None
                        else None
                    ),
                )
                for contributor in result.contributors
            ),
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            page_count=result.page_count,
            snapshot=encode_position(
                TimestampPosition(
                    result.snapshot.started_at,
                    result.snapshot.playback_id,
                )
                if result.snapshot is not None
                else None
            ),
        )

    return routes


def _entry_view(
    item: PlaybackHistoryEntry,
    application: Application,
) -> PlaybackHistoryEntryView:
    return PlaybackHistoryEntryView(
        playback_id=item.playback_id,
        request_id=item.request_id,
        track_id=item.track_id,
        title=item.title,
        artist_names=item.artist_names,
        artwork_url=item.artwork_url,
        duration_seconds=item.duration_seconds,
        origin=item.origin.value,
        requested_by=item.requested_by,
        radio_run_id=item.radio_run_id,
        source_id=item.source_id,
        source_url=item.source_url,
        source_provider=(
            item.source_provider.value if item.source_provider is not None else None
        ),
        contributor=(
            PlaybackContributorView(
                user_id=item.requested_by,
                display_name=item.contributor_display_name,
                discord_id=item.contributor_discord_id,
                discord_username=item.contributor_discord_username,
                avatar_url=application.integrations.avatars.public_url(
                    item.contributor_discord_id,
                    avatar_hash=item.contributor_discord_avatar_hash,
                ),
            )
            if item.contributor_discord_id is not None
            else None
        ),
        started_at=item.started_at,
        ended_at=item.ended_at,
        end_reason=item.end_reason.value if item.end_reason is not None else None,
        audio_seconds=item.audio_seconds,
        group_audio_seconds=item.group_audio_seconds,
    )


def _snapshot(value: str | None) -> PlaybackHistorySnapshot | None:
    position = decode_position(value)
    if position is None:
        return None
    return PlaybackHistorySnapshot(
        position.occurred_at,
        PlaybackRecordId(position.identifier),
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated personal Library and reaction endpoints."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import TrackId
from nahoermaar.library.domain import ReactionValue
from nahoermaar.library.read_model import (
    LibrarySnapshot,
    LibraryTrack,
    ReactionParticipant,
    ReactionSummary,
)

from .errors import ApiError, ApiErrorCode, error_responses
from .middleware import authenticated
from .pagination import (
    NumberedPageView,
    TimestampPosition,
    decode_position,
    encode_position,
)


class ReactionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: ReactionValue


class ReactionSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    reaction: ReactionValue | None
    likes: int
    dislikes: int


class ReactionSummariesView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[ReactionSummaryView, ...]


class LibraryTrackView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    title: str
    artist_names: tuple[str, ...]
    duration_seconds: float | None
    artwork_url: str | None
    album_title: str | None
    release_date: date | None
    isrc: str | None
    reaction: ReactionValue
    reacted_at: datetime
    likes: int
    dislikes: int


class LibraryTrackPageView(NumberedPageView[LibraryTrackView]):
    pass


class ReactionParticipantView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str
    avatar_url: str | None
    reaction: ReactionValue
    reacted_at: datetime


class ReactionParticipantPageView(NumberedPageView[ReactionParticipantView]):
    pass


def router(application: Application) -> APIRouter:
    """Build personal Library routes around the composed application."""
    routes = APIRouter(prefix="/api/library", tags=["library"])

    @routes.get(
        "/tracks",
        operation_id="listLibraryTracks",
        responses=error_responses(401, 422, 500, 503),
    )
    async def list_tracks(
        request: Request,
        reaction: ReactionValue = ReactionValue.LIKE,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        snapshot: str | None = None,
    ) -> LibraryTrackPageView:
        try:
            result = await application.library.service.tracks(
                authenticated(request).user.id,
                reaction=reaction,
                page=page,
                page_size=page_size,
                query=q,
                snapshot=_snapshot(snapshot),
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return LibraryTrackPageView(
            items=tuple(_track_view(item) for item in result.entries),
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            page_count=result.page_count,
            snapshot=_encoded_snapshot(result.snapshot),
        )

    @routes.get(
        "/reactions",
        operation_id="getLibraryReactionSummaries",
        responses=error_responses(401, 422, 500, 503),
    )
    async def summaries(
        request: Request,
        track_id: Annotated[list[UUID], Query(min_length=1, max_length=100)],
    ) -> ReactionSummariesView:
        try:
            result = await application.library.service.summaries(
                authenticated(request).user.id,
                tuple(TrackId(identifier) for identifier in track_id),
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return ReactionSummariesView(
            items=tuple(_summary_view(item) for item in result)
        )

    @routes.put(
        "/tracks/{track_id}/reaction",
        operation_id="setLibraryTrackReaction",
        responses=error_responses(401, 404, 422, 500, 503),
    )
    async def set_reaction(
        request: Request,
        track_id: UUID,
        body: ReactionUpdate,
    ) -> ReactionSummaryView:
        user_id = authenticated(request).user.id
        identifier = TrackId(track_id)
        await application.library.service.set_reaction(user_id, identifier, body.value)
        return _summary_view(
            (await application.library.service.summaries(user_id, (identifier,)))[0]
        )

    @routes.delete(
        "/tracks/{track_id}/reaction",
        operation_id="deleteLibraryTrackReaction",
        responses=error_responses(401, 422, 500, 503),
    )
    async def delete_reaction(
        request: Request,
        track_id: UUID,
    ) -> ReactionSummaryView:
        user_id = authenticated(request).user.id
        identifier = TrackId(track_id)
        await application.library.service.remove_reaction(user_id, identifier)
        return _summary_view(
            (await application.library.service.summaries(user_id, (identifier,)))[0]
        )

    @routes.get(
        "/tracks/{track_id}/reactions",
        operation_id="listLibraryTrackReactionParticipants",
        responses=error_responses(401, 404, 422, 500, 503),
    )
    async def participants(
        request: Request,
        track_id: UUID,
        reaction: ReactionValue = ReactionValue.LIKE,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        snapshot: str | None = None,
    ) -> ReactionParticipantPageView:
        authenticated(request)
        try:
            result = await application.library.service.participants(
                TrackId(track_id),
                reaction=reaction,
                page=page,
                page_size=page_size,
                snapshot=_snapshot(snapshot),
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return ReactionParticipantPageView(
            items=tuple(
                _participant_view(item, application) for item in result.entries
            ),
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            page_count=result.page_count,
            snapshot=_encoded_snapshot(result.snapshot),
        )

    return routes


def _summary_view(summary: ReactionSummary) -> ReactionSummaryView:
    return ReactionSummaryView(
        track_id=summary.track_id,
        reaction=summary.reaction,
        likes=summary.likes,
        dislikes=summary.dislikes,
    )


def _track_view(track: LibraryTrack) -> LibraryTrackView:
    return LibraryTrackView(
        track_id=track.track_id,
        title=track.title,
        artist_names=track.artist_names,
        duration_seconds=track.duration_seconds,
        artwork_url=track.artwork_url,
        album_title=track.album_title,
        release_date=track.release_date,
        isrc=track.isrc,
        reaction=track.reaction,
        reacted_at=track.reacted_at,
        likes=track.likes,
        dislikes=track.dislikes,
    )


def _participant_view(
    participant: ReactionParticipant,
    application: Application,
) -> ReactionParticipantView:
    return ReactionParticipantView(
        user_id=participant.user_id,
        display_name=participant.display_name,
        avatar_url=(
            application.integrations.avatars.public_url(
                participant.discord_id,
                avatar_hash=participant.discord_avatar_hash,
            )
            if participant.discord_id is not None
            else None
        ),
        reaction=participant.reaction,
        reacted_at=participant.reacted_at,
    )


def _snapshot(value: str | None) -> LibrarySnapshot | None:
    position = decode_position(value)
    if position is None:
        return None
    return LibrarySnapshot(position.occurred_at, position.identifier)


def _encoded_snapshot(snapshot: LibrarySnapshot | None) -> str | None:
    return encode_position(
        TimestampPosition(snapshot.updated_at, snapshot.identifier)
        if snapshot is not None
        else None
    )

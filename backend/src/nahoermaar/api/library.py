# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated personal Library and reaction endpoints."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import TrackId, TrackSourceId
from nahoermaar.library.domain import (
    PlaylistEntryId,
    PlaylistId,
    PlaylistTrackSelection,
    ReactionValue,
)
from nahoermaar.library.read_model import (
    LibraryContributor,
    LibrarySnapshot,
    LibraryTrack,
    PlaylistEntryItem,
    PlaylistSummary,
    ReactionParticipant,
    ReactionSummary,
)
from nahoermaar.messaging import MessageContext
from nahoermaar.player.domain import OperationId
from nahoermaar.player.events import AddTracks, TrackSelection

from .errors import ApiError, ApiErrorCode, error_responses
from .middleware import authenticated
from .pagination import (
    NumberedPageView,
    RevisionPosition,
    TimestampPosition,
    decode_position,
    decode_revision,
    encode_position,
    encode_revision,
)
from .player import MutationView, mutation_view

IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


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


class ContributorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str
    avatar_url: str | None


class PlaylistView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: UUID
    owner: ContributorView
    name: str
    revision: int
    entry_count: int
    artwork_urls: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class PlaylistPageView(NumberedPageView[PlaylistView]):
    pass


class PlaylistEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    playlist_id: UUID
    track_id: UUID
    preferred_source_id: UUID | None
    position: int
    title: str
    artist_names: tuple[str, ...]
    duration_seconds: float | None
    artwork_url: str | None
    album_title: str | None
    release_date: date | None
    isrc: str | None
    added_by: ContributorView
    added_at: datetime


class PlaylistEntryPageView(NumberedPageView[PlaylistEntryView]):
    pass


class PlaylistCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class PlaylistRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class PlaylistRenameInput(PlaylistRevisionInput):
    name: str = Field(min_length=1, max_length=100)


class PlaylistDuplicateInput(PlaylistRevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class PlaylistTrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: UUID
    preferred_source_id: UUID | None = None


class PlaylistEntriesInput(PlaylistRevisionInput):
    tracks: tuple[PlaylistTrackInput, ...] = Field(min_length=1, max_length=100)


class PlaylistOrderInput(PlaylistRevisionInput):
    entry_ids: tuple[UUID, ...] = Field(max_length=100)


class PlaylistDeletionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: UUID
    deleted: bool


def router(application: Application) -> APIRouter:
    """Build personal Library routes around the composed application."""
    routes = APIRouter(prefix="/api/library", tags=["library"])

    @routes.get(
        "/playlists",
        operation_id="listLibraryPlaylists",
        responses=error_responses(401, 422, 500, 503),
    )
    async def list_playlists(
        request: Request,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        snapshot: str | None = None,
    ) -> PlaylistPageView:
        try:
            result = await application.library.service.playlists(
                authenticated(request).user.id,
                page=page,
                page_size=page_size,
                query=q,
                snapshot=_snapshot(snapshot),
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return PlaylistPageView(
            items=tuple(_playlist_view(item, application) for item in result.entries),
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            page_count=result.page_count,
            snapshot=_encoded_snapshot(result.snapshot),
        )

    @routes.post(
        "/playlists",
        operation_id="createLibraryPlaylist",
        responses=error_responses(401, 422, 500, 503),
    )
    async def create_playlist(
        request: Request,
        body: PlaylistCreateInput,
    ) -> PlaylistView:
        try:
            playlist = await application.library.service.create_playlist(
                authenticated(request).user.id,
                body.name,
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return _playlist_view(playlist, application)

    @routes.get(
        "/playlists/{playlist_id}",
        operation_id="getLibraryPlaylist",
        responses=error_responses(401, 404, 422, 500, 503),
    )
    async def get_playlist(
        request: Request,
        playlist_id: UUID,
    ) -> PlaylistView:
        playlist = await application.library.service.playlist(
            authenticated(request).user.id,
            PlaylistId(playlist_id),
        )
        return _playlist_view(playlist, application)

    @routes.patch(
        "/playlists/{playlist_id}",
        operation_id="renameLibraryPlaylist",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def rename_playlist(
        request: Request,
        playlist_id: UUID,
        body: PlaylistRenameInput,
    ) -> PlaylistView:
        try:
            playlist = await application.library.service.rename_playlist(
                authenticated(request).user.id,
                PlaylistId(playlist_id),
                body.name,
                body.expected_revision,
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return _playlist_view(playlist, application)

    @routes.delete(
        "/playlists/{playlist_id}",
        operation_id="deleteLibraryPlaylist",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def delete_playlist(
        request: Request,
        playlist_id: UUID,
        body: PlaylistRevisionInput,
    ) -> PlaylistDeletionView:
        deleted = await application.library.service.delete_playlist(
            authenticated(request).user.id,
            PlaylistId(playlist_id),
            body.expected_revision,
        )
        return PlaylistDeletionView(playlist_id=deleted.id, deleted=True)

    @routes.post(
        "/playlists/{playlist_id}/duplicate",
        operation_id="duplicateLibraryPlaylist",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def duplicate_playlist(
        request: Request,
        playlist_id: UUID,
        body: PlaylistDuplicateInput,
    ) -> PlaylistView:
        try:
            playlist = await application.library.service.duplicate_playlist(
                authenticated(request).user.id,
                PlaylistId(playlist_id),
                name=body.name,
                expected_revision=body.expected_revision,
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return _playlist_view(playlist, application)

    @routes.get(
        "/playlists/{playlist_id}/entries",
        operation_id="listLibraryPlaylistEntries",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def list_playlist_entries(
        request: Request,
        playlist_id: UUID,
        q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        snapshot: str | None = None,
    ) -> PlaylistEntryPageView:
        identifier = PlaylistId(playlist_id)
        try:
            result = await application.library.service.playlist_entries(
                authenticated(request).user.id,
                identifier,
                page=page,
                page_size=page_size,
                query=q,
                revision=_playlist_revision(snapshot, identifier),
            )
        except ValueError as error:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422) from error
        return PlaylistEntryPageView(
            items=tuple(
                _playlist_entry_view(item, application) for item in result.entries
            ),
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            page_count=result.page_count,
            snapshot=encode_revision(RevisionPosition(playlist_id, result.revision)),
        )

    @routes.post(
        "/playlists/{playlist_id}/entries",
        operation_id="addLibraryPlaylistEntries",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def add_playlist_entries(
        request: Request,
        playlist_id: UUID,
        body: PlaylistEntriesInput,
    ) -> PlaylistView:
        playlist = await application.library.service.add_playlist_entries(
            authenticated(request).user.id,
            PlaylistId(playlist_id),
            tuple(
                PlaylistTrackSelection(
                    TrackId(item.track_id),
                    TrackSourceId(item.preferred_source_id)
                    if item.preferred_source_id is not None
                    else None,
                )
                for item in body.tracks
            ),
            body.expected_revision,
        )
        return _playlist_view(playlist, application)

    @routes.delete(
        "/playlists/{playlist_id}/entries/{entry_id}",
        operation_id="deleteLibraryPlaylistEntry",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def delete_playlist_entry(
        request: Request,
        playlist_id: UUID,
        entry_id: UUID,
        body: PlaylistRevisionInput,
    ) -> PlaylistView:
        playlist = await application.library.service.remove_playlist_entry(
            authenticated(request).user.id,
            PlaylistId(playlist_id),
            PlaylistEntryId(entry_id),
            body.expected_revision,
        )
        return _playlist_view(playlist, application)

    @routes.put(
        "/playlists/{playlist_id}/order",
        operation_id="replaceLibraryPlaylistOrder",
        responses=error_responses(401, 404, 409, 422, 500, 503),
    )
    async def replace_playlist_order(
        request: Request,
        playlist_id: UUID,
        body: PlaylistOrderInput,
    ) -> PlaylistView:
        playlist = await application.library.service.reorder_playlist(
            authenticated(request).user.id,
            PlaylistId(playlist_id),
            tuple(PlaylistEntryId(entry_id) for entry_id in body.entry_ids),
            body.expected_revision,
        )
        return _playlist_view(playlist, application)

    @routes.post(
        "/playlists/{playlist_id}/queue",
        operation_id="queueLibraryPlaylist",
        responses=error_responses(401, 403, 404, 409, 422, 500, 503),
    )
    async def queue_playlist(
        request: Request,
        playlist_id: UUID,
        body: PlaylistRevisionInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        selections = await application.library.service.playlist_selections(
            current.user.id,
            PlaylistId(playlist_id),
            expected_revision=body.expected_revision,
        )
        if not selections:
            raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422)
        result = await application.bus.execute(
            AddTracks(
                application.player.service.state.session.id,
                OperationId(idempotency_key),
                tuple(
                    TrackSelection(selection.track_id, selection.preferred_source_id)
                    for selection in selections
                ),
                skip_duplicates=False,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await mutation_view(application, result)

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


def _contributor_view(
    contributor: LibraryContributor,
    application: Application,
) -> ContributorView:
    return ContributorView(
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


def _playlist_view(
    playlist: PlaylistSummary,
    application: Application,
) -> PlaylistView:
    return PlaylistView(
        playlist_id=playlist.playlist_id,
        owner=_contributor_view(playlist.owner, application),
        name=playlist.name,
        revision=playlist.revision,
        entry_count=playlist.entry_count,
        artwork_urls=playlist.artwork_urls,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )


def _playlist_entry_view(
    entry: PlaylistEntryItem,
    application: Application,
) -> PlaylistEntryView:
    return PlaylistEntryView(
        entry_id=entry.entry_id,
        playlist_id=entry.playlist_id,
        track_id=entry.track_id,
        preferred_source_id=entry.preferred_source_id,
        position=entry.position,
        title=entry.title,
        artist_names=entry.artist_names,
        duration_seconds=entry.duration_seconds,
        artwork_url=entry.artwork_url,
        album_title=entry.album_title,
        release_date=entry.release_date,
        isrc=entry.isrc,
        added_by=_contributor_view(entry.added_by, application),
        added_at=entry.added_at,
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


def _playlist_revision(value: str | None, playlist_id: PlaylistId) -> int | None:
    position = decode_revision(value)
    if position is None:
        return None
    if position.resource_id != playlist_id:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422)
    return position.revision

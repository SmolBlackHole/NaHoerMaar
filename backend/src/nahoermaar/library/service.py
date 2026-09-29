# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated use cases for personal track reactions."""

from collections.abc import Callable
from datetime import UTC, datetime

from nahoermaar.catalog.domain import TrackId
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.users.domain import UserId

from .domain import (
    MAX_PLAYLIST_MUTATION_ENTRIES,
    MAX_PLAYLIST_NAME_LENGTH,
    LibraryError,
    LibraryErrorCode,
    Playlist,
    PlaylistAccess,
    PlaylistEntryId,
    PlaylistEntryUndo,
    PlaylistEntryUndoId,
    PlaylistId,
    PlaylistScope,
    PlaylistTrackSelection,
    PlaylistVisibility,
    ReactionValue,
    TrackReaction,
    playlist_name,
)
from .read_model import (
    LibraryContributor,
    LibraryReadModel,
    LibrarySnapshot,
    LibraryTrackPage,
    PlaylistEntryPage,
    PlaylistPage,
    PlaylistSummary,
    ReactionParticipantPage,
    ReactionSummary,
)
from .repository import PlaylistRepository, TrackReactionRepository
from .synchronization import PlaylistSynchronizer

type Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LibraryService:
    """Own personal reactions, playlists and Library read contracts."""

    __slots__ = ("_catalog", "_clock", "_reader", "_synchronizer", "_units")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        catalog: CatalogService,
        reader: LibraryReadModel,
        synchronizer: PlaylistSynchronizer,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._catalog = catalog
        self._reader = reader
        self._synchronizer = synchronizer
        self._clock = clock

    async def set_reaction(
        self,
        user_id: UserId,
        track_id: TrackId,
        value: ReactionValue,
    ) -> TrackReaction:
        await self._require_track(track_id)
        async with self._units() as work:
            reaction = await TrackReactionRepository(work.session).set(
                user_id, track_id, value, self._clock()
            )
            await work.commit()
        return reaction

    async def remove_reaction(self, user_id: UserId, track_id: TrackId) -> bool:
        async with self._units() as work:
            removed = await TrackReactionRepository(work.session).remove(
                user_id, track_id
            )
            await work.commit()
        return removed

    async def summaries(
        self,
        user_id: UserId,
        track_ids: tuple[TrackId, ...],
    ) -> tuple[ReactionSummary, ...]:
        if not 1 <= len(track_ids) <= 100:
            raise ValueError("Reaction summaries require between 1 and 100 tracks.")
        return await self._reader.summaries(user_id, track_ids)

    async def tracks(
        self,
        user_id: UserId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> LibraryTrackPage:
        return await self._reader.tracks(
            user_id,
            reaction=reaction,
            page=page,
            page_size=page_size,
            query=query,
            snapshot=snapshot,
        )

    async def profile_tracks(
        self,
        subject_id: UserId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> LibraryTrackPage:
        await self._require_user(subject_id)
        return await self._reader.tracks(
            subject_id,
            reaction=reaction,
            page=page,
            page_size=page_size,
            query=query,
            snapshot=snapshot,
        )

    async def participants(
        self,
        track_id: TrackId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        snapshot: LibrarySnapshot | None = None,
    ) -> ReactionParticipantPage:
        await self._require_track(track_id)
        return await self._reader.participants(
            track_id,
            reaction=reaction,
            page=page,
            page_size=page_size,
            snapshot=snapshot,
        )

    async def contributors(
        self,
        actor_id: UserId,
        *,
        query: str,
        limit: int = 10,
    ) -> tuple[LibraryContributor, ...]:
        return await self._reader.contributors(actor_id, query=query, limit=limit)

    async def create_playlist(self, owner_id: UserId, name: str) -> PlaylistSummary:
        normalized = playlist_name(name)
        async with self._units() as work:
            playlist = await PlaylistRepository(work.session).create(
                owner_id,
                normalized,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(owner_id, playlist.id)

    async def import_playlist(
        self,
        owner_id: UserId,
        source_url: str,
        *,
        name: str | None = None,
    ) -> PlaylistSummary:
        materialized = await self._catalog.materialize_playlist(source_url)
        normalized = (
            playlist_name(name)
            if name is not None
            else playlist_name(
                (materialized.title or "Imported playlist")[:MAX_PLAYLIST_NAME_LENGTH]
            )
        )
        selections = tuple(
            PlaylistTrackSelection(entry.track.id, entry.source.id)
            for entry in materialized.entries
        )
        now = self._clock()
        async with self._units() as work:
            playlist, _created = await PlaylistRepository(work.session).import_linked(
                owner_id,
                normalized,
                provider_key=materialized.provider_key,
                external_id=materialized.external_id,
                canonical_url=materialized.canonical_url,
                selections=selections,
                unavailable_entry_count=materialized.unavailable_entry_count,
                truncated=materialized.truncated,
                now=now,
            )
            await work.commit()
        return await self._reader.playlist(owner_id, playlist.id)

    async def playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
    ) -> PlaylistSummary:
        return await self._reader.playlist(actor_id, playlist_id)

    async def playlists(
        self,
        actor_id: UserId,
        *,
        scope: PlaylistScope = PlaylistScope.OWNED,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> PlaylistPage:
        return await self._reader.playlists(
            actor_id,
            scope=scope,
            page=page,
            page_size=page_size,
            query=query,
            snapshot=snapshot,
        )

    async def profile_playlists(
        self,
        actor_id: UserId,
        subject_id: UserId,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> PlaylistPage:
        await self._require_user(subject_id)
        return await self._reader.playlists(
            actor_id,
            scope=PlaylistScope.PUBLIC,
            owner_id=subject_id,
            page=page,
            page_size=page_size,
            query=query,
            snapshot=snapshot,
        )

    async def update_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        name: str | None,
        visibility: PlaylistVisibility | None,
        expected_revision: int,
    ) -> PlaylistSummary:
        if name is None and visibility is None:
            raise ValueError("A playlist update requires a name or visibility.")
        normalized = playlist_name(name) if name is not None else None
        async with self._units() as work:
            await PlaylistRepository(work.session).update(
                actor_id,
                playlist_id,
                name=normalized,
                visibility=visibility,
                expected_revision=expected_revision,
                now=self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def move_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        position: int,
    ) -> PlaylistSummary:
        async with self._units() as work:
            await PlaylistRepository(work.session).move_playlist(
                actor_id,
                playlist_id,
                position,
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def delete_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        expected_revision: int,
    ) -> Playlist:
        async with self._units() as work:
            deleted = await PlaylistRepository(work.session).delete(
                actor_id,
                playlist_id,
                expected_revision,
            )
            await work.commit()
        return deleted

    async def duplicate_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        name: str | None,
        expected_revision: int,
    ) -> PlaylistSummary:
        source = await self._reader.playlist(actor_id, playlist_id)
        suffix = " copy"
        default_name = (
            f"{source.name[: MAX_PLAYLIST_NAME_LENGTH - len(suffix)]}{suffix}"
        )
        normalized = playlist_name(name or default_name)
        async with self._units() as work:
            duplicate = await PlaylistRepository(work.session).duplicate(
                actor_id,
                playlist_id,
                normalized,
                expected_revision,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, duplicate.id)

    async def playlist_entries(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
        revision: int | None = None,
    ) -> PlaylistEntryPage:
        return await self._reader.playlist_entries(
            actor_id,
            playlist_id,
            page=page,
            page_size=page_size,
            query=query,
            revision=revision,
        )

    async def add_playlist_entries(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        selections: tuple[PlaylistTrackSelection, ...],
        expected_revision: int,
    ) -> PlaylistSummary:
        await self._validate_selections(selections)
        async with self._units() as work:
            await PlaylistRepository(work.session).add(
                actor_id,
                playlist_id,
                selections,
                actor_id,
                expected_revision,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def remove_playlist_entry(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        entry_id: PlaylistEntryId,
        expected_revision: int,
    ) -> tuple[PlaylistSummary, PlaylistEntryUndo]:
        async with self._units() as work:
            _playlist, undo = await PlaylistRepository(work.session).remove(
                actor_id,
                playlist_id,
                entry_id,
                expected_revision,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id), undo

    async def undo_playlist_entry(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        undo_id: PlaylistEntryUndoId,
        expected_revision: int,
    ) -> PlaylistSummary:
        async with self._units() as work:
            await PlaylistRepository(work.session).restore(
                actor_id,
                playlist_id,
                undo_id,
                expected_revision,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def move_playlist_entry(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        entry_id: PlaylistEntryId,
        position: int,
        expected_revision: int,
    ) -> PlaylistSummary:
        async with self._units() as work:
            await PlaylistRepository(work.session).move(
                actor_id,
                playlist_id,
                entry_id,
                position,
                expected_revision,
                self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def detach_playlist_source(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        expected_revision: int,
    ) -> PlaylistSummary:
        async with self._units() as work:
            await PlaylistRepository(work.session).detach(
                actor_id,
                playlist_id,
                expected_revision=expected_revision,
                now=self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def synchronize_playlist(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
    ) -> PlaylistSummary:
        summary = await self._reader.playlist(actor_id, playlist_id)
        if summary.access is not PlaylistAccess.OWNER:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ACCESS_DENIED, 403)
        if summary.source is None:
            raise LibraryError(LibraryErrorCode.PLAYLIST_NOT_LINKED, 409)
        await self._synchronizer.synchronize(
            Playlist(
                summary.playlist_id,
                summary.owner.user_id,
                summary.position,
                summary.name,
                summary.visibility,
                summary.source,
                summary.revision,
                summary.created_at,
                summary.updated_at,
            ),
            now=self._clock(),
        )
        return await self._reader.playlist(actor_id, playlist_id)

    async def playlist_selections(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        *,
        expected_revision: int | None = None,
    ) -> tuple[PlaylistTrackSelection, ...]:
        async with self._units() as work:
            return await PlaylistRepository(work.session).selections(
                actor_id,
                playlist_id,
                expected_revision=expected_revision,
            )

    async def add_playlist_collaborator(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        collaborator_id: UserId,
        *,
        expected_revision: int,
    ) -> PlaylistSummary:
        playlist = await self._reader.playlist(actor_id, playlist_id)
        if playlist.access is not PlaylistAccess.OWNER:
            raise LibraryError(LibraryErrorCode.PLAYLIST_ACCESS_DENIED, 403)
        if not await self._reader.user_exists(collaborator_id):
            raise LibraryError(LibraryErrorCode.USER_NOT_FOUND, 404)
        async with self._units() as work:
            await PlaylistRepository(work.session).add_collaborator(
                actor_id,
                playlist_id,
                collaborator_id,
                expected_revision=expected_revision,
                now=self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def remove_playlist_collaborator(
        self,
        actor_id: UserId,
        playlist_id: PlaylistId,
        collaborator_id: UserId,
        *,
        expected_revision: int,
    ) -> PlaylistSummary:
        async with self._units() as work:
            await PlaylistRepository(work.session).remove_collaborator(
                actor_id,
                playlist_id,
                collaborator_id,
                expected_revision=expected_revision,
                now=self._clock(),
            )
            await work.commit()
        return await self._reader.playlist(actor_id, playlist_id)

    async def _validate_selections(
        self,
        selections: tuple[PlaylistTrackSelection, ...],
    ) -> None:
        if not 1 <= len(selections) <= MAX_PLAYLIST_MUTATION_ENTRIES:
            raise ValueError(
                "Playlist additions require between 1 and "
                f"{MAX_PLAYLIST_MUTATION_ENTRIES} tracks."
            )
        tracks = await self._catalog.tracks(
            {selection.track_id for selection in selections}
        )
        for selection in selections:
            track = tracks.get(selection.track_id)
            if track is None:
                raise LibraryError(LibraryErrorCode.TRACK_NOT_FOUND, 404)
            if selection.preferred_source_id is not None and all(
                source.id != selection.preferred_source_id for source in track.sources
            ):
                raise LibraryError(LibraryErrorCode.TRACK_SOURCE_NOT_FOUND, 404)

    async def _require_track(self, track_id: TrackId) -> None:
        if track_id not in await self._catalog.tracks({track_id}):
            raise LibraryError(LibraryErrorCode.TRACK_NOT_FOUND, 404)

    async def _require_user(self, user_id: UserId) -> None:
        if not await self._reader.user_exists(user_id):
            raise LibraryError(LibraryErrorCode.USER_NOT_FOUND, 404)

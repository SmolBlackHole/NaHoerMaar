# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.routing import APIRoute
import pytest
from sqlalchemy import func, insert, select

from nahoermaar.api import library as library_api
from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import (
    MediaKind,
    MediaReference,
    ProviderName,
    TrackId,
    TrackSourceId,
)
from nahoermaar.catalog.providers import (
    ProviderArtist,
    ProviderAudio,
    ProviderError,
    ProviderPage,
    ProviderPlaylist,
    ProviderTrack,
)
from nahoermaar.catalog.repository import CatalogRepository
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.library.domain import (
    LibraryError,
    LibraryErrorCode,
    PlaylistAccess,
    PlaylistScope,
    PlaylistTrackSelection,
    PlaylistVisibility,
    ReactionValue,
)
from nahoermaar.library.read_model import LibraryReadModel
from nahoermaar.library.maintenance import PlaylistSyncMaintenance
from nahoermaar.library.repository import PlaylistRepository
from nahoermaar.library.service import LibraryService
from nahoermaar.library.synchronization import PlaylistSynchronizer
from nahoermaar.messaging import MessageContext
from nahoermaar.operations.jobs import (
    JobRunDetailOutcome,
    JobTrigger,
)
from nahoermaar.operations.scheduler import (
    JobExecution,
    JobOptions,
    JobProgressUnit,
)
from nahoermaar.player.domain import ListeningSessionId, OperationId
from nahoermaar.player.events import AddTracks, TrackSelection
from nahoermaar.users.domain import (
    AccessRole,
    Authenticated,
    DiscordIdentity,
    User,
    UserId,
)
from nahoermaar.views.catalog import CatalogCleanupView

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLlibrarysource"
SECOND_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLlibrarysecond"


class PlaylistProvider:
    key = "youtube"

    def __init__(self, tracks: tuple[ProviderTrack, ...]) -> None:
        self.tracks = tracks

    def identify(
        self,
        source_url: str,
        *,
        kind: MediaKind | None = None,
    ) -> MediaReference | None:
        if source_url != PLAYLIST_URL or kind not in {None, MediaKind.PLAYLIST}:
            return None
        return MediaReference(
            ProviderName.YOUTUBE,
            "PLlibrarysource",
            MediaKind.PLAYLIST,
            PLAYLIST_URL,
        )

    async def playlist(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPlaylist:
        assert reference.external_id == "PLlibrarysource"
        assert limit >= 1
        if continuation is None:
            return ProviderPlaylist(
                reference,
                "Imported source",
                ProviderPage((self.tracks[0], self.tracks[0]), "second", 1),
            )
        assert continuation == "second"
        return ProviderPlaylist(
            reference, "Imported source", ProviderPage((self.tracks[1],))
        )

    async def search(
        self,
        query: str,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        del query, limit, continuation
        return ProviderPage(())

    async def track(self, reference: MediaReference) -> ProviderTrack:
        del reference
        return self.tracks[0]

    async def radio(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        del reference, limit, continuation
        return ProviderPage(())

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        del reference
        return ProviderAudio("https://audio.example/stream")

    async def close(self) -> None:
        return None


class SyncPlaylistProvider:
    key = "youtube"

    def __init__(
        self,
        contents: dict[str, tuple[ProviderTrack, ...]],
    ) -> None:
        self.contents = contents
        self.failures: set[str] = set()
        self.active_requests = 0
        self.maximum_active_requests = 0

    def identify(
        self,
        source_url: str,
        *,
        kind: MediaKind | None = None,
    ) -> MediaReference | None:
        if source_url not in self.contents or kind not in {None, MediaKind.PLAYLIST}:
            return None
        return MediaReference(
            ProviderName.YOUTUBE,
            source_url.rsplit("=", maxsplit=1)[-1],
            MediaKind.PLAYLIST,
            source_url,
        )

    async def playlist(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPlaylist:
        assert continuation is None
        self.active_requests += 1
        self.maximum_active_requests = max(
            self.maximum_active_requests,
            self.active_requests,
        )
        try:
            await asyncio.sleep(0)
            if reference.external_id in self.failures:
                raise ProviderError("playlist unavailable")
            entries = self.contents[reference.source_url]
            assert len(entries) <= limit
            return ProviderPlaylist(
                reference,
                f"Playlist {reference.external_id}",
                ProviderPage(entries),
            )
        finally:
            self.active_requests -= 1

    async def search(
        self,
        query: str,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        del query, limit, continuation
        return ProviderPage(())

    async def track(self, reference: MediaReference) -> ProviderTrack:
        return self.contents[reference.source_url][0]

    async def radio(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        del reference, limit, continuation
        return ProviderPage(())

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        del reference
        return ProviderAudio("https://audio.example/stream")

    async def close(self) -> None:
        return None


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def _track(index: int, title: str, artist: str) -> ProviderTrack:
    external_id = f"library{index:04d}"
    return ProviderTrack(
        provider=ProviderName.YOUTUBE,
        external_id=external_id,
        source_url=f"https://music.youtube.com/watch?v={external_id}",
        title=title,
        artist_text=artist,
        artists=(
            ProviderArtist(
                ProviderName.YOUTUBE_MUSIC,
                f"library-artist-{index}",
                artist,
            ),
        ),
        duration_seconds=180.0 + index,
        artwork_url=f"https://img.example/{index}.jpg",
        isrc=f"DELIB2600{index:03d}",
    )


async def _add_user(
    work: UnitOfWork,
    user_id: UserId,
    discord_id: str,
    display_name: str,
) -> None:
    await work.session.execute(
        insert(Base.metadata.tables["users"]).values(
            id=user_id,
            role="owner" if discord_id == "1" else "admin",
            access_granted_by=None,
            access_granted_at=None,
            created_at=NOW,
            updated_at=NOW,
            last_login_at=None,
        )
    )
    await work.session.execute(
        insert(Base.metadata.tables["discord_identities"]).values(
            user_id=user_id,
            discord_id=discord_id,
            username=display_name.casefold(),
            avatar_hash=f"avatar-{discord_id}",
            synced_at=NOW,
        )
    )
    await work.session.execute(
        insert(Base.metadata.tables["user_profiles"]).values(
            user_id=user_id,
            display_name=display_name,
            updated_at=NOW,
        )
    )


def test_reactions_are_idempotent_personal_queryable_and_cleanup_safe() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        first_user = UserId(uuid4())
        second_user = UserId(uuid4())
        async with units() as work:
            await _add_user(work, first_user, "1", "Andrey")
            await _add_user(work, second_user, "2", "Kai")
            catalog_repository = CatalogRepository(work.session)
            tracks = tuple(
                [
                    await catalog_repository.upsert(
                        _track(1, "Still Alive", "Mt Eden"), NOW
                    ),
                    await catalog_repository.upsert(
                        _track(2, "Yamakasi", "Miyagi & Andy Panda"), NOW
                    ),
                    await catalog_repository.upsert(
                        _track(3, "Love End", "Dray Freero"), NOW
                    ),
                    await catalog_repository.upsert(
                        _track(4, "Empty Room", "skyfall beats"), NOW
                    ),
                ]
            )
            await work.commit()

        clock = [NOW]
        catalog = CatalogService(units, ())
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )

        first_like = await library.set_reaction(
            first_user, tracks[0].id, ReactionValue.LIKE
        )
        clock[0] += timedelta(minutes=1)
        repeated_like = await library.set_reaction(
            first_user, tracks[0].id, ReactionValue.LIKE
        )
        assert repeated_like.updated_at == first_like.updated_at

        changed = await library.set_reaction(
            first_user, tracks[0].id, ReactionValue.DISLIKE
        )
        assert changed.updated_at == clock[0]
        await library.set_reaction(second_user, tracks[0].id, ReactionValue.LIKE)
        summary = (await library.summaries(first_user, (tracks[0].id,)))[0]
        assert summary.reaction is ReactionValue.DISLIKE
        assert (summary.likes, summary.dislikes) == (1, 1)

        assert await library.remove_reaction(first_user, tracks[0].id)
        assert not await library.remove_reaction(first_user, tracks[0].id)
        removed = (await library.summaries(first_user, (tracks[0].id,)))[0]
        assert removed.reaction is None
        assert (removed.likes, removed.dislikes) == (1, 0)

        for index, track in enumerate(tracks[:3], start=1):
            clock[0] += timedelta(minutes=index)
            await library.set_reaction(first_user, track.id, ReactionValue.LIKE)

        by_title = await library.tracks(
            first_user,
            reaction=ReactionValue.LIKE,
            page=1,
            page_size=20,
            query="Yamakasi",
        )
        assert tuple(item.track_id for item in by_title.entries) == (tracks[1].id,)
        by_artist = await library.tracks(
            first_user,
            reaction=ReactionValue.LIKE,
            page=1,
            page_size=20,
            query="Mt Eden",
        )
        assert tuple(item.track_id for item in by_artist.entries) == (tracks[0].id,)

        first_page = await library.tracks(
            first_user,
            reaction=ReactionValue.LIKE,
            page=1,
            page_size=2,
        )
        assert first_page.total == 3
        assert first_page.page_count == 2
        assert first_page.snapshot is not None
        clock[0] += timedelta(minutes=10)
        await library.set_reaction(first_user, tracks[3].id, ReactionValue.LIKE)
        second_page = await library.tracks(
            first_user,
            reaction=ReactionValue.LIKE,
            page=2,
            page_size=2,
            snapshot=first_page.snapshot,
        )
        assert second_page.total == 3
        assert len(second_page.entries) == 1

        participants = await library.participants(
            tracks[0].id,
            reaction=ReactionValue.LIKE,
            page=1,
            page_size=20,
        )
        assert participants.total == 2
        assert {entry.display_name for entry in participants.entries} == {
            "Andrey",
            "Kai",
        }

        async with units() as work:
            candidates = await CatalogCleanupView().candidates(
                work.session,
                checked_before=NOW + timedelta(days=90),
                limit=20,
            )
        reacted_ids = {track.id for track in tracks}
        assert reacted_ids.isdisjoint(item.track_id for item in candidates.sources)
        assert reacted_ids.isdisjoint(item.id for item in candidates.tracks)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_personal_playlists_preserve_order_revisions_and_catalog_references() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        owner = UserId(uuid4())
        other = UserId(uuid4())
        async with units() as work:
            await _add_user(work, owner, "101", "Andrey")
            await _add_user(work, other, "102", "Kai")
            repository = CatalogRepository(work.session)
            tracks = tuple(
                [
                    await repository.upsert(_track(101, "Still Alive", "Mt Eden"), NOW),
                    await repository.upsert(
                        _track(102, "Yamakasi", "Miyagi & Andy Panda"), NOW
                    ),
                    await repository.upsert(
                        _track(103, "Love End", "Dray Freero"), NOW
                    ),
                    await repository.upsert(
                        _track(104, "Empty Room", "skyfall beats"), NOW
                    ),
                    await repository.upsert(_track(105, "Sacrifice", "shxpe"), NOW),
                ]
            )
            await work.commit()

        clock = [NOW]
        catalog = CatalogService(units, ())
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )

        playlist = await library.create_playlist(owner, "Road test")
        assert playlist.revision == 0
        clock[0] += timedelta(minutes=1)
        playlist = await library.update_playlist(
            owner,
            playlist.playlist_id,
            name="Road favourites",
            visibility=None,
            expected_revision=playlist.revision,
        )
        selections = (
            PlaylistTrackSelection(tracks[0].id, tracks[0].sources[0].id),
            PlaylistTrackSelection(tracks[0].id, tracks[0].sources[0].id),
            PlaylistTrackSelection(tracks[1].id, tracks[1].sources[0].id),
            PlaylistTrackSelection(tracks[2].id, tracks[2].sources[0].id),
            PlaylistTrackSelection(tracks[3].id, tracks[3].sources[0].id),
        )
        clock[0] += timedelta(minutes=1)
        playlist = await library.add_playlist_entries(
            owner,
            playlist.playlist_id,
            selections,
            playlist.revision,
        )
        assert playlist.entry_count == 5
        assert playlist.artwork_urls == tuple(track.artwork_url for track in tracks[:4])

        entries = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
        )
        assert tuple(entry.track_id for entry in entries.entries) == tuple(
            selection.track_id for selection in selections
        )
        assert tuple(entry.position for entry in entries.entries) == tuple(range(5))
        assert {entry.added_by.user_id for entry in entries.entries} == {owner}
        assert (
            await library.playlist_entries(
                owner,
                playlist.playlist_id,
                page=1,
                page_size=20,
                query="Miyagi",
                revision=entries.revision,
            )
        ).total == 1

        with pytest.raises(LibraryError) as hidden:
            await library.playlist(other, playlist.playlist_id)
        assert hidden.value.code is LibraryErrorCode.PLAYLIST_NOT_FOUND

        moved_ids = (
            entries.entries[-1].entry_id,
            *(entry.entry_id for entry in entries.entries[:-1]),
        )
        clock[0] += timedelta(minutes=1)
        playlist = await library.move_playlist_entry(
            owner,
            playlist.playlist_id,
            entries.entries[-1].entry_id,
            0,
            entries.revision,
        )
        reordered = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        assert tuple(entry.entry_id for entry in reordered.entries) == moved_ids

        with pytest.raises(LibraryError) as stale_page:
            await library.playlist_entries(
                owner,
                playlist.playlist_id,
                page=1,
                page_size=100,
                revision=entries.revision,
            )
        assert stale_page.value.code is LibraryErrorCode.PLAYLIST_REVISION_CONFLICT
        with pytest.raises(LibraryError) as stale_mutation:
            await library.add_playlist_entries(
                owner,
                playlist.playlist_id,
                (PlaylistTrackSelection(tracks[4].id),),
                entries.revision,
            )
        assert stale_mutation.value.code is LibraryErrorCode.PLAYLIST_REVISION_CONFLICT
        assert (
            await library.playlist_entries(
                owner,
                playlist.playlist_id,
                page=1,
                page_size=100,
                revision=playlist.revision,
            )
        ).total == 5

        clock[0] += timedelta(minutes=1)
        removed_entry = reordered.entries[-1]
        playlist, undo = await library.remove_playlist_entry(
            owner,
            playlist.playlist_id,
            removed_entry.entry_id,
            playlist.revision,
        )
        assert undo.entry.id == removed_entry.entry_id
        assert undo.entry.playlist_id == removed_entry.playlist_id
        assert undo.entry.track_id == removed_entry.track_id
        assert undo.entry.preferred_source_id == removed_entry.preferred_source_id
        assert undo.entry.added_by == removed_entry.added_by.user_id
        assert undo.entry.position == removed_entry.position
        assert undo.entry.created_at == removed_entry.added_at
        assert undo.actor_id == owner
        remaining = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        assert tuple(entry.position for entry in remaining.entries) == tuple(range(4))

        playlist = await library.undo_playlist_entry(
            owner,
            playlist.playlist_id,
            undo.id,
            playlist.revision,
        )
        restored = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        assert tuple(entry.entry_id for entry in restored.entries) == tuple(
            entry.entry_id for entry in reordered.entries
        )
        assert restored.entries[-1] == removed_entry
        with pytest.raises(LibraryError) as reused_undo:
            await library.undo_playlist_entry(
                owner,
                playlist.playlist_id,
                undo.id,
                playlist.revision,
            )
        assert reused_undo.value.code is LibraryErrorCode.PLAYLIST_UNDO_UNAVAILABLE

        playlist, expired_undo = await library.remove_playlist_entry(
            owner,
            playlist.playlist_id,
            removed_entry.entry_id,
            playlist.revision,
        )
        clock[0] += timedelta(seconds=13)
        with pytest.raises(LibraryError) as expired:
            await library.undo_playlist_entry(
                owner,
                playlist.playlist_id,
                expired_undo.id,
                playlist.revision,
            )
        assert expired.value.code is LibraryErrorCode.PLAYLIST_UNDO_UNAVAILABLE

        remaining = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        async with units() as work:
            removed_undos = await PlaylistRepository(work.session).prune_entry_undos(
                clock[0],
                limit=100,
            )
            await work.commit()
        assert removed_undos == 1

        duplicate = await library.duplicate_playlist(
            owner,
            playlist.playlist_id,
            name=None,
            expected_revision=playlist.revision,
        )
        copied = await library.playlist_entries(
            owner,
            duplicate.playlist_id,
            page=1,
            page_size=100,
        )
        assert duplicate.name == "Road favourites copy"
        assert tuple(entry.track_id for entry in copied.entries) == tuple(
            entry.track_id for entry in remaining.entries
        )
        assert tuple(entry.entry_id for entry in copied.entries) != tuple(
            entry.entry_id for entry in remaining.entries
        )
        assert {entry.added_by.user_id for entry in copied.entries} == {owner}
        assert (
            await library.playlists(
                owner,
                page=1,
                page_size=20,
                query="favourites copy",
            )
        ).total == 1
        assert await library.playlist_selections(
            owner,
            playlist.playlist_id,
            expected_revision=playlist.revision,
        ) == tuple(
            PlaylistTrackSelection(entry.track_id, entry.preferred_source_id)
            for entry in remaining.entries
        )

        capacity = await library.create_playlist(owner, "Capacity")
        for _ in range(10):
            capacity = await library.add_playlist_entries(
                owner,
                capacity.playlist_id,
                tuple(PlaylistTrackSelection(tracks[4].id) for _ in range(100)),
                capacity.revision,
            )
        with pytest.raises(LibraryError) as overflow:
            await library.add_playlist_entries(
                owner,
                capacity.playlist_id,
                (PlaylistTrackSelection(tracks[4].id),),
                capacity.revision,
            )
        assert overflow.value.code is LibraryErrorCode.PLAYLIST_CAPACITY_EXCEEDED
        full = await library.playlist_entries(
            owner,
            capacity.playlist_id,
            page=1,
            page_size=100,
            revision=capacity.revision,
        )
        assert full.total == 1_000

        async with units() as work:
            candidates = await CatalogCleanupView().candidates(
                work.session,
                checked_before=NOW + timedelta(days=90),
                limit=100,
            )
        protected_tracks = {entry.track_id for entry in remaining.entries}
        protected_sources = {
            entry.preferred_source_id
            for entry in remaining.entries
            if entry.preferred_source_id is not None
        }
        assert protected_tracks.isdisjoint(
            candidate.track_id for candidate in candidates.sources
        )
        assert protected_tracks.isdisjoint(
            candidate.id for candidate in candidates.tracks
        )
        assert protected_sources.isdisjoint(
            candidate.id for candidate in candidates.sources
        )

        await library.delete_playlist(
            owner,
            duplicate.playlist_id,
            duplicate.revision,
        )
        async with units() as work:
            duplicate_entries = int(
                await work.session.scalar(
                    select(func.count())
                    .select_from(Base.metadata.tables["playlist_entries"])
                    .where(
                        Base.metadata.tables["playlist_entries"].c.playlist_id
                        == duplicate.playlist_id
                    )
                )
                or 0
            )
            catalog_tracks = int(
                await work.session.scalar(
                    select(func.count()).select_from(Base.metadata.tables["tracks"])
                )
                or 0
            )
        assert duplicate_entries == 0
        assert catalog_tracks >= len(tracks)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_owned_playlist_order_is_persistent_and_does_not_change_public_order() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        owner = UserId(uuid4())
        reader = UserId(uuid4())
        async with units() as work:
            await _add_user(work, owner, "111", "Owner")
            await _add_user(work, reader, "112", "Reader")
            await work.commit()

        clock = [NOW]
        catalog = CatalogService(units, ())
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )

        first = await library.create_playlist(owner, "First")
        clock[0] += timedelta(minutes=1)
        first = await library.update_playlist(
            owner,
            first.playlist_id,
            name=None,
            visibility=PlaylistVisibility.PUBLIC,
            expected_revision=first.revision,
        )
        clock[0] += timedelta(minutes=1)
        second = await library.create_playlist(owner, "Second")
        clock[0] += timedelta(minutes=1)
        second = await library.update_playlist(
            owner,
            second.playlist_id,
            name=None,
            visibility=PlaylistVisibility.PUBLIC,
            expected_revision=second.revision,
        )

        await library.move_playlist(owner, second.playlist_id, 0)
        moved = await library.move_playlist(owner, first.playlist_id, 0)
        assert moved.position == 0
        assert moved.revision == first.revision
        assert moved.updated_at == first.updated_at

        owned = await library.playlists(owner, page=1, page_size=20)
        assert tuple(item.playlist_id for item in owned.entries) == (
            first.playlist_id,
            second.playlist_id,
        )
        assert tuple(item.position for item in owned.entries) == (0, 1)

        public = await library.playlists(
            reader,
            scope=PlaylistScope.PUBLIC,
            page=1,
            page_size=20,
        )
        assert tuple(item.playlist_id for item in public.entries) == (
            second.playlist_id,
            first.playlist_id,
        )

        with pytest.raises(LibraryError) as denied:
            await library.move_playlist(reader, first.playlist_id, 1)
        assert denied.value.code is LibraryErrorCode.PLAYLIST_ACCESS_DENIED
        with pytest.raises(LibraryError) as invalid:
            await library.move_playlist(owner, first.playlist_id, 2)
        assert invalid.value.code is LibraryErrorCode.PLAYLIST_ORDER_INVALID

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_linked_playlist_import_is_deterministic_read_only_and_detachable() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        owner = UserId(uuid4())
        async with units() as work:
            await _add_user(work, owner, "151", "Andrey")
            await work.commit()

        clock = [NOW]
        provider_tracks = (
            _track(151, "Repeated song", "Loop Artist"),
            _track(152, "Last song", "Final Artist"),
        )
        catalog = CatalogService(units, (PlaylistProvider(provider_tracks),))
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )

        playlist = await library.import_playlist(owner, PLAYLIST_URL)
        assert playlist.name == "Imported source"
        assert playlist.revision == 0
        assert playlist.entry_count == 3
        assert playlist.source is not None
        assert playlist.source.provider_key == "youtube"
        assert playlist.source.external_id == "PLlibrarysource"
        assert playlist.source.canonical_url == PLAYLIST_URL
        assert playlist.source.unavailable_entry_count == 1
        assert playlist.source.truncated is False

        entries = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
        )
        assert tuple(entry.position for entry in entries.entries) == (0, 1, 2)
        assert entries.entries[0].track_id == entries.entries[1].track_id
        assert entries.entries[0].entry_id != entries.entries[1].entry_id
        assert entries.entries[2].track_id != entries.entries[0].track_id

        repeated = await library.import_playlist(
            owner,
            PLAYLIST_URL,
            name="Ignored on deterministic reimport",
        )
        assert repeated.playlist_id == playlist.playlist_id
        assert repeated.name == "Imported source"
        assert repeated.revision == playlist.revision

        selection = PlaylistTrackSelection(
            entries.entries[2].track_id,
            entries.entries[2].preferred_source_id,
        )
        with pytest.raises(LibraryError) as linked_add:
            await library.add_playlist_entries(
                owner,
                playlist.playlist_id,
                (selection,),
                playlist.revision,
            )
        assert linked_add.value.code is LibraryErrorCode.PLAYLIST_LINKED_READ_ONLY
        with pytest.raises(LibraryError) as linked_remove:
            await library.remove_playlist_entry(
                owner,
                playlist.playlist_id,
                entries.entries[0].entry_id,
                playlist.revision,
            )
        assert linked_remove.value.code is LibraryErrorCode.PLAYLIST_LINKED_READ_ONLY
        with pytest.raises(LibraryError) as linked_move:
            await library.move_playlist_entry(
                owner,
                playlist.playlist_id,
                entries.entries[2].entry_id,
                0,
                playlist.revision,
            )
        assert linked_move.value.code is LibraryErrorCode.PLAYLIST_LINKED_READ_ONLY

        clock[0] += timedelta(minutes=1)
        synchronized = await library.synchronize_playlist(
            owner,
            playlist.playlist_id,
        )
        assert synchronized.revision == playlist.revision
        assert synchronized.source is not None
        assert synchronized.source.last_attempt_at == clock[0]
        assert synchronized.source.last_error_code is None

        manual = await library.create_playlist(owner, "Manual")
        with pytest.raises(LibraryError) as not_linked:
            await library.synchronize_playlist(owner, manual.playlist_id)
        assert not_linked.value.code is LibraryErrorCode.PLAYLIST_NOT_LINKED

        clock[0] += timedelta(minutes=1)
        detached = await library.detach_playlist_source(
            owner,
            playlist.playlist_id,
            synchronized.revision,
        )
        assert detached.source is None
        assert detached.revision == 1
        detached_entries = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=detached.revision,
        )
        assert tuple(entry.entry_id for entry in detached_entries.entries) == tuple(
            entry.entry_id for entry in entries.entries
        )

        clock[0] += timedelta(minutes=1)
        moved = await library.move_playlist_entry(
            owner,
            playlist.playlist_id,
            detached_entries.entries[2].entry_id,
            0,
            detached.revision,
        )
        assert moved.revision == 2
        reordered = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=moved.revision,
        )
        assert reordered.entries[0].entry_id == detached_entries.entries[2].entry_id
        assert tuple(entry.position for entry in reordered.entries) == (0, 1, 2)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_playlist_sync_job_reconciles_occurrences_and_keeps_failed_cache() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        owner = UserId(uuid4())
        async with units() as work:
            await _add_user(work, owner, "181", "Andrey")
            await work.commit()

        first_track = _track(181, "First", "Sync Artist")
        retained_track = _track(182, "Retained", "Sync Artist")
        failed_track = _track(183, "Cached", "Other Artist")
        added_track = _track(184, "Added", "Sync Artist")
        provider = SyncPlaylistProvider(
            {
                PLAYLIST_URL: (first_track, retained_track),
                SECOND_PLAYLIST_URL: (failed_track,),
            }
        )
        clock = [NOW]
        catalog = CatalogService(units, (provider,), clock=lambda: clock[0])
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )
        first = await library.import_playlist(owner, PLAYLIST_URL)
        second = await library.import_playlist(owner, SECOND_PLAYLIST_URL)
        first_before = await library.playlist_entries(
            owner,
            first.playlist_id,
            page=1,
            page_size=100,
        )
        second_before = await library.playlist_entries(
            owner,
            second.playlist_id,
            page=1,
            page_size=100,
        )

        provider.contents[PLAYLIST_URL] = (retained_track, added_track)
        provider.failures.add("PLlibrarysecond")
        clock[0] += timedelta(hours=2)
        progress: list[tuple[int, int, JobProgressUnit]] = []
        maintenance = PlaylistSyncMaintenance(
            units,
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
            parallel_requests=1,
        )

        def execution() -> JobExecution:
            return JobExecution(
                JobOptions(batch_size=10),
                JobTrigger.SCHEDULED,
                None,
                lambda current, total, unit: progress.append((current, total, unit)),
            )

        first_run = await maintenance.run(execution())
        assert first_run.candidate_count == 2
        assert first_run.processed_count == 2
        assert first_run.changed_count == 1
        assert first_run.failure_count == 1
        assert first_run.error_code == "playlist_sync_partial"
        assert provider.maximum_active_requests == 1
        assert progress[-1] == (2, 2, JobProgressUnit.RECORDS)

        details = {detail.subject_id: detail for detail in first_run.details}
        changed_detail = details[str(first.playlist_id)]
        assert changed_detail.outcome is JobRunDetailOutcome.CHANGED
        assert changed_detail.summary == (
            "1 added, 1 removed, 1 moved, 0 unavailable, 0 unchanged."
        )
        assert changed_detail.affected_count == 3
        failed_detail = details[str(second.playlist_id)]
        assert failed_detail.outcome is JobRunDetailOutcome.FAILED
        assert failed_detail.error_code == "provider_failed"

        first_after = await library.playlist(
            owner,
            first.playlist_id,
        )
        first_entries = await library.playlist_entries(
            owner,
            first.playlist_id,
            page=1,
            page_size=100,
        )
        assert first_after.revision == first.revision + 1
        assert first_entries.entries[0].track_id == first_before.entries[1].track_id
        assert first_entries.entries[1].track_id not in {
            entry.track_id for entry in first_before.entries
        }
        assert first_entries.entries[0].entry_id == first_before.entries[1].entry_id
        assert first_entries.entries[1].entry_id not in {
            entry.entry_id for entry in first_before.entries
        }

        second_after = await library.playlist(owner, second.playlist_id)
        second_entries = await library.playlist_entries(
            owner,
            second.playlist_id,
            page=1,
            page_size=100,
        )
        assert second_after.revision == second.revision
        assert second_after.source is not None
        assert second_after.source.last_error_code == "provider_failed"
        assert tuple(entry.entry_id for entry in second_entries.entries) == tuple(
            entry.entry_id for entry in second_before.entries
        )

        immediate = await maintenance.run(execution())
        assert immediate.candidate_count == 0
        assert immediate.details == ()

        provider.failures.clear()
        clock[0] += timedelta(hours=1)
        recovered = await maintenance.run(execution())
        assert recovered.candidate_count == 2
        assert recovered.changed_count == 0
        assert recovered.failure_count == 0
        assert all(
            detail.outcome is JobRunDetailOutcome.UNCHANGED
            for detail in recovered.details
        )
        recovered_first = await library.playlist(owner, first.playlist_id)
        recovered_second = await library.playlist(owner, second.playlist_id)
        assert recovered_first.revision == first_after.revision
        assert recovered_second.revision == second.revision
        assert recovered_second.source is not None
        assert recovered_second.source.last_error_code is None
        await catalog.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_playlist_visibility_collaboration_and_scopes_enforce_capabilities() -> None:
    database = _database()

    async def scenario() -> None:
        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        owner = UserId(uuid4())
        editor = UserId(uuid4())
        reader = UserId(uuid4())
        stranger = UserId(uuid4())
        async with units() as work:
            await _add_user(work, owner, "201", "Owner")
            await _add_user(work, editor, "202", "Editor")
            await _add_user(work, reader, "203", "Reader")
            await _add_user(work, stranger, "204", "Stranger")
            track = await CatalogRepository(work.session).upsert(
                _track(201, "Shared Song", "Collaboration"), NOW
            )
            await work.commit()

        clock = [NOW]
        catalog = CatalogService(units, ())
        library = LibraryService(
            units,
            catalog,
            LibraryReadModel(units),
            PlaylistSynchronizer(units, catalog),
            clock=lambda: clock[0],
        )

        playlist = await library.create_playlist(owner, "Shared later")
        assert playlist.visibility is PlaylistVisibility.PRIVATE
        assert playlist.access is PlaylistAccess.OWNER
        assert playlist.collaborators == ()
        matches = await library.contributors(owner, query="dit", limit=10)
        assert tuple(match.user_id for match in matches) == (editor,)
        assert await library.contributors(owner, query="Owner", limit=10) == ()
        assert (await library.playlists(owner, page=1, page_size=20)).total == 1
        assert (
            await library.playlists(
                editor,
                scope=PlaylistScope.SHARED,
                page=1,
                page_size=20,
            )
        ).total == 0

        clock[0] += timedelta(minutes=1)
        playlist = await library.add_playlist_collaborator(
            owner,
            playlist.playlist_id,
            editor,
            expected_revision=playlist.revision,
        )
        assert playlist.revision == 1
        assert tuple(item.user_id for item in playlist.collaborators) == (editor,)
        with pytest.raises(LibraryError) as duplicate:
            await library.add_playlist_collaborator(
                owner,
                playlist.playlist_id,
                editor,
                expected_revision=playlist.revision,
            )
        assert duplicate.value.code is LibraryErrorCode.PLAYLIST_COLLABORATOR_EXISTS
        assert (await library.playlist(owner, playlist.playlist_id)).revision == 1

        clock[0] += timedelta(minutes=1)
        playlist = await library.update_playlist(
            owner,
            playlist.playlist_id,
            name=None,
            visibility=PlaylistVisibility.COLLABORATORS,
            expected_revision=playlist.revision,
        )
        editor_view = await library.playlist(editor, playlist.playlist_id)
        assert editor_view.access is PlaylistAccess.EDITOR
        shared = await library.playlists(
            editor,
            scope=PlaylistScope.SHARED,
            page=1,
            page_size=20,
        )
        assert tuple(item.playlist_id for item in shared.entries) == (
            playlist.playlist_id,
        )
        with pytest.raises(LibraryError) as hidden:
            await library.playlist(stranger, playlist.playlist_id)
        assert hidden.value.code is LibraryErrorCode.PLAYLIST_NOT_FOUND
        hidden_page = await library.playlists(
            stranger,
            scope=PlaylistScope.PUBLIC,
            page=1,
            page_size=1,
            query="Shared later",
        )
        assert hidden_page.total == 0
        assert hidden_page.page_count == 0
        assert hidden_page.entries == ()

        clock[0] += timedelta(minutes=1)
        playlist = await library.add_playlist_entries(
            editor,
            playlist.playlist_id,
            (PlaylistTrackSelection(track.id, track.sources[0].id),),
            playlist.revision,
        )
        entries = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=20,
            revision=playlist.revision,
        )
        assert entries.entries[0].added_by.user_id == editor
        with pytest.raises(LibraryError) as editor_management:
            await library.update_playlist(
                editor,
                playlist.playlist_id,
                name="Editor rename",
                visibility=None,
                expected_revision=playlist.revision,
            )
        assert editor_management.value.code is LibraryErrorCode.PLAYLIST_ACCESS_DENIED
        assert (
            await library.playlist(owner, playlist.playlist_id)
        ).name == "Shared later"

        clock[0] += timedelta(minutes=1)
        playlist = await library.update_playlist(
            owner,
            playlist.playlist_id,
            name=None,
            visibility=PlaylistVisibility.PUBLIC,
            expected_revision=playlist.revision,
        )
        reader_view = await library.playlist(reader, playlist.playlist_id)
        assert reader_view.access is PlaylistAccess.READER
        public = await library.playlists(
            stranger,
            scope=PlaylistScope.PUBLIC,
            page=1,
            page_size=20,
        )
        assert tuple(item.playlist_id for item in public.entries) == (
            playlist.playlist_id,
        )
        assert (
            len(
                await library.playlist_selections(
                    reader,
                    playlist.playlist_id,
                    expected_revision=playlist.revision,
                )
            )
            == 1
        )
        with pytest.raises(LibraryError) as reader_write:
            await library.add_playlist_entries(
                reader,
                playlist.playlist_id,
                (PlaylistTrackSelection(track.id),),
                playlist.revision,
            )
        assert reader_write.value.code is LibraryErrorCode.PLAYLIST_ACCESS_DENIED
        assert (await library.playlist(owner, playlist.playlist_id)).revision == 4

        clock[0] += timedelta(minutes=1)
        playlist = await library.remove_playlist_collaborator(
            owner,
            playlist.playlist_id,
            editor,
            expected_revision=playlist.revision,
        )
        assert playlist.collaborators == ()
        assert (
            await library.playlist(editor, playlist.playlist_id)
        ).access is PlaylistAccess.READER
        with pytest.raises(LibraryError) as revoked_write:
            await library.remove_playlist_entry(
                editor,
                playlist.playlist_id,
                entries.entries[0].entry_id,
                playlist.revision,
            )
        assert revoked_write.value.code is LibraryErrorCode.PLAYLIST_ACCESS_DENIED

        clock[0] += timedelta(minutes=1)
        playlist = await library.update_playlist(
            owner,
            playlist.playlist_id,
            name=None,
            visibility=PlaylistVisibility.COLLABORATORS,
            expected_revision=playlist.revision,
        )
        with pytest.raises(LibraryError) as revoked_hidden:
            await library.playlist(editor, playlist.playlist_id)
        assert revoked_hidden.value.code is LibraryErrorCode.PLAYLIST_NOT_FOUND
        assert (
            await library.playlists(
                editor,
                scope=PlaylistScope.SHARED,
                page=1,
                page_size=1,
                query="Shared later",
            )
        ).total == 0
        with pytest.raises(LibraryError) as stale_grant:
            await library.add_playlist_collaborator(
                owner,
                playlist.playlist_id,
                editor,
                expected_revision=playlist.revision - 1,
            )
        assert stale_grant.value.code is LibraryErrorCode.PLAYLIST_REVISION_CONFLICT
        assert (await library.playlist(owner, playlist.playlist_id)).revision == 6

        with pytest.raises(LibraryError) as owner_collaborator:
            await library.add_playlist_collaborator(
                owner,
                playlist.playlist_id,
                owner,
                expected_revision=playlist.revision,
            )
        assert (
            owner_collaborator.value.code
            is LibraryErrorCode.PLAYLIST_COLLABORATOR_INVALID
        )
        assert (await library.playlist(owner, playlist.playlist_id)).revision == 6

        missing_user = UserId(uuid4())
        with pytest.raises(LibraryError) as missing:
            await library.add_playlist_collaborator(
                owner,
                playlist.playlist_id,
                missing_user,
                expected_revision=playlist.revision,
            )
        assert missing.value.code is LibraryErrorCode.USER_NOT_FOUND
        assert (await library.playlist(owner, playlist.playlist_id)).revision == 6

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_queue_playlist_api_sends_one_ordered_attributed_player_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = UserId(uuid4())
    playlist_id = uuid4()
    session_id = ListeningSessionId(uuid4())
    operation_id = uuid4()
    source_id = TrackSourceId(uuid4())
    selections = (
        PlaylistTrackSelection(TrackId(uuid4()), None),
        PlaylistTrackSelection(TrackId(uuid4()), source_id),
        PlaylistTrackSelection(TrackId(uuid4()), None),
    )

    class Library:
        async def playlist_selections(
            self,
            owner_id: UserId,
            requested_playlist_id: object,
            *,
            expected_revision: int | None = None,
        ) -> tuple[PlaylistTrackSelection, ...]:
            assert owner_id == user_id
            assert requested_playlist_id == playlist_id
            assert expected_revision == 7
            return selections

    class Bus:
        def __init__(self) -> None:
            self.calls: list[tuple[AddTracks, MessageContext]] = []

        async def execute(
            self,
            command: AddTracks,
            context: MessageContext,
        ) -> object:
            self.calls.append((command, context))
            return marker

    marker = object()
    bus = Bus()
    application = cast(
        Application,
        SimpleNamespace(
            library=SimpleNamespace(service=Library()),
            player=SimpleNamespace(
                service=SimpleNamespace(
                    state=SimpleNamespace(session=SimpleNamespace(id=session_id))
                )
            ),
            bus=bus,
        ),
    )

    async def return_reply(_application: Application, reply: object) -> object:
        return reply

    monkeypatch.setattr(library_api, "mutation_view", return_reply)
    endpoint = cast(
        Any,
        next(
            route.endpoint
            for route in library_api.router(application).routes
            if isinstance(route, APIRoute)
            and route.operation_id == "queueLibraryPlaylist"
        ),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/library/playlists/{playlist_id}/queue",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 123),
        }
    )
    request.state.authenticated = Authenticated(
        User(
            user_id,
            DiscordIdentity("101", "andrey", synced_at=NOW),
            NOW,
            NOW,
            role=AccessRole.OWNER,
        ),
        NOW + timedelta(hours=1),
        "csrf",
    )

    result = asyncio.run(
        endpoint(
            request,
            playlist_id,
            library_api.PlaylistRevisionInput(expected_revision=7),
            operation_id,
        )
    )

    assert result is marker
    assert len(bus.calls) == 1
    command, context = bus.calls[0]
    assert command.session_id == session_id
    assert command.operation_id == OperationId(operation_id)
    assert command.selections == (
        TrackSelection(selections[0].track_id),
        TrackSelection(selections[1].track_id, source_id),
        TrackSelection(selections[2].track_id),
    )
    assert not command.skip_duplicates
    assert context.actor_id == user_id

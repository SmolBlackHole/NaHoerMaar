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
from nahoermaar.catalog.domain import ProviderName, TrackId, TrackSourceId
from nahoermaar.catalog.providers import ProviderArtist, ProviderTrack
from nahoermaar.catalog.repository import CatalogRepository
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.library.domain import (
    LibraryError,
    LibraryErrorCode,
    PlaylistTrackSelection,
    ReactionValue,
)
from nahoermaar.library.read_model import LibraryReadModel
from nahoermaar.library.service import LibraryService
from nahoermaar.messaging import MessageContext
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
        library = LibraryService(
            units,
            CatalogService(units, ()),
            LibraryReadModel(units),
            clock=lambda: clock[0],
        )

        playlist = await library.create_playlist(owner, "Road test")
        assert playlist.revision == 0
        clock[0] += timedelta(minutes=1)
        playlist = await library.rename_playlist(
            owner,
            playlist.playlist_id,
            "Road favourites",
            playlist.revision,
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

        reversed_ids = tuple(entry.entry_id for entry in reversed(entries.entries))
        clock[0] += timedelta(minutes=1)
        playlist = await library.reorder_playlist(
            owner,
            playlist.playlist_id,
            reversed_ids,
            entries.revision,
        )
        reordered = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        assert tuple(entry.entry_id for entry in reordered.entries) == reversed_ids

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
        playlist = await library.remove_playlist_entry(
            owner,
            playlist.playlist_id,
            reordered.entries[-1].entry_id,
            playlist.revision,
        )
        remaining = await library.playlist_entries(
            owner,
            playlist.playlist_id,
            page=1,
            page_size=100,
            revision=playlist.revision,
        )
        assert tuple(entry.position for entry in remaining.entries) == tuple(range(4))

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
        assert full.total == 100

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

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import (
    Artist,
    ArtistIdentity,
    MediaIdentity,
    Track,
)
from nahormaar_backend.engine.persistence import ArtistRepository, Base, TrackRepository

from .database import isolated_database

type ArtistCatalog = tuple[tuple[Artist, Artist], tuple[Track, Track]]


@pytest.fixture
def catalog() -> ArtistCatalog:
    artists = (
        Artist(ArtistIdentity("youtube", "UCFirst"), "Artist, with comma"),
        Artist(ArtistIdentity("youtube", "UCSecond"), "Амура"),
    )
    created_at = datetime(2026, 9, 21, tzinfo=UTC)
    first = Track(
        MediaIdentity("youtube", "first-video"),
        "https://www.youtube.com/watch?v=first-video",
        TrackMetadata(title="Я хочу любить", artist="Original, unsplit credits"),
        created_at=created_at,
        updated_at=created_at,
        artist_ids=tuple(artist.id for artist in artists),
    )
    second = replace(
        first,
        id=uuid4(),
        identity=MediaIdentity("youtube", "second-video"),
        source_url="https://www.youtube.com/watch?v=second-video",
        artist_ids=(artists[0].id,),
    )
    return artists, (first, second)


async def store_catalog(session: AsyncSession, catalog: ArtistCatalog) -> None:
    artists, tracks = catalog
    for artist in artists:
        await ArtistRepository(session).add(artist)
    for track in tracks:
        await TrackRepository(session).add(track)


def test_shared_ordered_credits_survive_reopen_as_detached_domain_values(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    artists, tracks = catalog

    async def scenario() -> None:
        path = tmp_path / "artists.db"
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                repository = ArtistRepository(session)
                for artist in artists:
                    assert await repository.get(artist.id) == artist
                    assert await repository.find(artist.identity) == artist
                assert await repository.get(uuid4()) is None
                assert (
                    await repository.find(ArtistIdentity("youtube", "missing")) is None
                )
                restored = await TrackRepository(session).get(tracks[0].id)
                assert restored is not None
                assert (
                    await TrackRepository(session).find(tracks[1].identity) == tracks[1]
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            Base.metadata.tables["artists"]
                        )
                    )
                    == 2
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            Base.metadata.tables["track_artists"]
                        )
                    )
                    == 3
                )
        assert restored == tracks[0]
        assert restored.artist_ids == (artists[0].id, artists[1].id)
        assert restored.metadata.artist == "Original, unsplit credits"

    asyncio.run(scenario())


def test_equal_names_and_case_variants_remain_distinct_provider_identities(
    tmp_path: Path,
) -> None:
    artists = (
        Artist(ArtistIdentity("youtube", "UCExample"), "Same name"),
        Artist(ArtistIdentity("youtube", "ucexample"), "Same name"),
        Artist(ArtistIdentity("another-source", "UCExample"), "Same name"),
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                for artist in artists:
                    await ArtistRepository(session).add(artist)
            async with sessions.begin() as session:
                for artist in artists:
                    assert (
                        await ArtistRepository(session).find(artist.identity) == artist
                    )

    asyncio.run(scenario())


def test_artist_rename_preserves_references_and_original_track_text(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    artists, tracks = catalog
    renamed = replace(artists[0], name="Новое имя")

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
            async with sessions.begin() as session:
                repository = ArtistRepository(session)
                await repository.update(renamed)
                assert await repository.get(renamed.id) == renamed
                with pytest.raises(ValueError, match="identity cannot change"):
                    await repository.update(
                        replace(renamed, identity=artists[1].identity)
                    )
                with pytest.raises(LookupError, match="Unknown artist"):
                    await repository.update(replace(renamed, id=uuid4()))
            async with sessions.begin() as session:
                assert await ArtistRepository(session).find(renamed.identity) == renamed
                for track in tracks:
                    assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_credit_reorder_and_removal_do_not_delete_shared_artists(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    artists, (track, other) = catalog
    orders = (
        (artists[1].id, artists[0].id),
        (artists[1].id,),
        (),
        track.artist_ids,
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
            for order in orders:
                changed = replace(track, artist_ids=order)
                async with sessions.begin() as session:
                    repository = TrackRepository(session)
                    previous = await repository.get(track.id)
                    await repository.update(changed)
                    assert await repository.get(track.id) == changed
                    assert previous is not None
                async with sessions.begin() as session:
                    assert (
                        await TrackRepository(session).find(track.identity) == changed
                    )
                    assert await TrackRepository(session).get(other.id) == other
                    for artist in artists:
                        assert await ArtistRepository(session).get(artist.id) == artist
            async with sessions.begin() as session:
                refreshed = replace(
                    track,
                    metadata=track.metadata.merge(TrackMetadata(duration_seconds=245)),
                    updated_at=track.updated_at + timedelta(seconds=1),
                )
                await TrackRepository(session).update(refreshed)
                assert await TrackRepository(session).get(track.id) == refreshed

    asyncio.run(scenario())


@pytest.mark.parametrize("duplicate", ["identity", "id"])
def test_duplicate_artist_rolls_back_track_and_artist_changes(
    tmp_path: Path, catalog: ArtistCatalog, duplicate: str
) -> None:
    artists, (track, _) = catalog
    conflicting = (
        replace(artists[0], id=uuid4())
        if duplicate == "identity"
        else replace(artists[0], identity=ArtistIdentity("youtube", "another"))
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await TrackRepository(session).update(replace(track, artist_ids=()))
                    await ArtistRepository(session).update(
                        replace(artists[1], name="Uncommitted")
                    )
                    await ArtistRepository(session).add(conflicting)
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == track
                for artist in artists:
                    assert await ArtistRepository(session).get(artist.id) == artist

    asyncio.run(scenario())


def test_missing_artist_rolls_back_replacement_credits_and_timestamps(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    _, (track, _) = catalog
    new_artist = Artist(ArtistIdentity("youtube", "UCNew"), "New artist")
    invalid = replace(
        track,
        artist_ids=(new_artist.id, uuid4()),
        updated_at=track.updated_at + timedelta(hours=1),
        checked_at=track.updated_at + timedelta(hours=1),
        metadata=replace(track.metadata, title="Uncommitted"),
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await ArtistRepository(session).add(new_artist)
                    await TrackRepository(session).update(invalid)
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == track
                assert await ArtistRepository(session).get(new_artist.id) is None

    asyncio.run(scenario())


def test_application_rollback_discards_artist_track_and_credit_inserts(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            with pytest.raises(RuntimeError, match="Abort operation"):
                async with sessions.begin() as session:
                    await store_catalog(session, catalog)
                    raise RuntimeError("Abort operation")
            async with sessions.begin() as session:
                for name in ("artists", "tracks", "track_artists"):
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(Base.metadata.tables[name])
                        )
                        == 0
                    )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid",
    ["missing_track", "duplicate_artist", "duplicate_position", "negative_position"],
)
def test_credit_table_enforces_ownership_uniqueness_and_position(
    tmp_path: Path, catalog: ArtistCatalog, invalid: str
) -> None:
    artists, (track, _) = catalog
    uncredited = Artist(ArtistIdentity("youtube", "UCUncredited"), "Another")
    track_id = uuid4() if invalid == "missing_track" else track.id
    artist_id = artists[0].id if invalid == "duplicate_artist" else uncredited.id
    position = {
        "missing_track": 0,
        "duplicate_artist": 2,
        "duplicate_position": 0,
        "negative_position": -1,
    }[invalid]

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
                await ArtistRepository(session).add(uncredited)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(
                        insert(Base.metadata.tables["track_artists"]).values(
                            track_id=track_id, artist_id=artist_id, position=position
                        )
                    )
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_deleting_a_track_removes_only_its_credits_and_protects_shared_artists(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    artists, (track, other) = catalog

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await store_catalog(session, catalog)
            artist_table = Base.metadata.tables["artists"]
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await session.execute(
                        delete(artist_table).where(artist_table.c.id == artists[0].id)
                    )
            track_table = Base.metadata.tables["tracks"]
            async with sessions.begin() as session:
                await session.execute(
                    delete(track_table).where(track_table.c.id == track.id)
                )
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) is None
                assert await TrackRepository(session).get(other.id) == other
                for artist in artists:
                    assert await ArtistRepository(session).get(artist.id) == artist
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            Base.metadata.tables["track_artists"]
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_legacy_artist_text_does_not_invent_entities(
    tmp_path: Path, catalog: ArtistCatalog
) -> None:
    _, (track, _) = catalog
    legacy = replace(track, artist_ids=())

    async def scenario() -> None:
        async with isolated_database(tmp_path / "artists.db") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(legacy)
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(legacy.id) == legacy
                for table_name in ("artists", "track_artists"):
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table_name]
                            )
                        )
                        == 0
                    )

    asyncio.run(scenario())

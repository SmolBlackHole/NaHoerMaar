# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.persistence import TrackRepository

from .database import isolated_database


@pytest.fixture
def track() -> Track:
    created_at = datetime(2026, 9, 21, tzinfo=UTC)
    return Track(
        created_at=created_at,
        updated_at=created_at,
        id=UUID("00000000-0000-0000-0000-000000000001"),
        identity=MediaIdentity("youtube", "GCYGuZGE6DA"),
        source_url="https://www.youtube.com/watch?v=GCYGuZGE6DA",
        metadata=TrackMetadata(
            title="Амура - Я хочу любить (MRJay Remix)",
            artist="Artist, with comma",
            uploader="Uploader",
            uploader_url="https://www.youtube.com/@artist",
            duration_seconds=245.5,
            thumbnail_url="https://i.ytimg.com/vi/GCYGuZGE6DA/hqdefault.jpg",
        ),
    )


def test_committed_track_survives_reopening_the_database(
    tmp_path: Path, track: Track
) -> None:
    async def scenario() -> None:
        path = tmp_path / "tracks.sqlite3"
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)

        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == track
                assert await repository.find(track.identity) == track
                assert await repository.get(UUID(int=0)) is None
                assert (
                    await repository.find(MediaIdentity("youtube", "missing")) is None
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "metadata",
    [
        TrackMetadata(),
        TrackMetadata(title="Only a title"),
        TrackMetadata(duration_seconds=0),
    ],
)
def test_partial_metadata_roundtrip(
    tmp_path: Path, track: Track, metadata: TrackMetadata
) -> None:
    track = replace(track, metadata=metadata)

    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == track

    asyncio.run(scenario())


def test_update_persists_domain_merge_and_returns_detached_values(
    tmp_path: Path, track: Track
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                loaded = await repository.get(track.id)
                assert loaded is not None
                updated = replace(
                    loaded,
                    metadata=loaded.metadata.merge(
                        TrackMetadata(title="Corrected title"), overwrite=True
                    ),
                )
                await repository.update(updated)
                assert await repository.get(track.id) == updated
                assert loaded == track
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(track.id) == updated
            assert updated.metadata.artist == track.metadata.artist
            assert updated.metadata.duration_seconds == track.metadata.duration_seconds

    asyncio.run(scenario())


def test_external_ids_are_case_sensitive_and_namespaced(
    tmp_path: Path, track: Track
) -> None:
    variants = (
        track,
        replace(
            track,
            id=uuid4(),
            identity=MediaIdentity("youtube", track.identity.external_id.lower()),
        ),
        replace(
            track,
            id=uuid4(),
            identity=MediaIdentity("another-source", track.identity.external_id),
        ),
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                for variant in variants:
                    await repository.add(variant)
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                for variant in variants:
                    assert await repository.find(variant.identity) == variant

    asyncio.run(scenario())


def test_duplicate_identity_rolls_back_other_writes(
    tmp_path: Path, track: Track
) -> None:
    new_track = replace(
        track, id=uuid4(), identity=MediaIdentity("youtube", "different")
    )
    duplicate = replace(
        track,
        id=uuid4(),
        source_url="https://music.youtube.com/watch?v=GCYGuZGE6DA",
    )

    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    repository = TrackRepository(session)
                    await repository.add(new_track)
                    await repository.add(duplicate)
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == track
                assert await repository.find(track.identity) == track
                assert await repository.get(duplicate.id) is None
                assert await repository.get(new_track.id) is None

    asyncio.run(scenario())


def test_duplicate_internal_id_does_not_replace_a_track(
    tmp_path: Path, track: Track
) -> None:
    duplicate = replace(track, identity=MediaIdentity("youtube", "different"))

    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    await TrackRepository(session).add(duplicate)
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == track
                assert await repository.find(duplicate.identity) is None

    asyncio.run(scenario())


def test_application_failure_rolls_back_insert_and_update(
    tmp_path: Path, track: Track
) -> None:
    new_track = replace(
        track, id=uuid4(), identity=MediaIdentity("youtube", "different")
    )
    updated = replace(track, metadata=TrackMetadata(title="Uncommitted title"))

    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            with pytest.raises(RuntimeError, match="Abort operation"):
                async with sessions.begin() as session:
                    repository = TrackRepository(session)
                    await repository.update(updated)
                    await repository.add(new_track)
                    raise RuntimeError("Abort operation")
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == track
                assert await repository.get(new_track.id) is None

    asyncio.run(scenario())


def test_repository_does_not_commit_or_close_the_callers_session(
    tmp_path: Path, track: Track
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions() as writer:
                async with writer.begin():
                    repository = TrackRepository(writer)
                    await repository.add(track)
                    assert await repository.get(track.id) == track
                    async with sessions.begin() as reader:
                        assert await TrackRepository(reader).get(track.id) is None
                    await writer.rollback()
                async with writer.begin():
                    assert await TrackRepository(writer).get(track.id) is None
                    await TrackRepository(writer).add(track)
            async with sessions.begin() as reader:
                assert await TrackRepository(reader).get(track.id) == track

    asyncio.run(scenario())


def test_update_rejects_identity_reassignment_and_unknown_tracks(
    tmp_path: Path, track: Track
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "tracks.sqlite3") as sessions:
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                await repository.add(track)
                with pytest.raises(ValueError, match="identity cannot change"):
                    await repository.update(
                        replace(track, identity=MediaIdentity("youtube", "different"))
                    )
                with pytest.raises(ValueError, match="creation time cannot change"):
                    await repository.update(
                        replace(
                            track, created_at=track.created_at - timedelta(seconds=1)
                        )
                    )
                with pytest.raises(LookupError, match="Unknown track"):
                    await repository.update(replace(track, id=uuid4()))
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == track
                assert (
                    await repository.find(MediaIdentity("youtube", "different")) is None
                )

    asyncio.run(scenario())


def test_track_timestamps_roundtrip_as_utc_and_checks_do_not_imply_changes(
    tmp_path: Path, track: Track
) -> None:
    created_at = datetime(2026, 9, 21, 12, tzinfo=timezone(timedelta(hours=2)))
    track = replace(
        track,
        created_at=created_at,
        updated_at=created_at,
        checked_at=created_at - timedelta(seconds=2),
    )
    checked = replace(track, checked_at=created_at + timedelta(hours=1))
    updated = replace(
        checked,
        metadata=replace(track.metadata, title="Corrected title"),
        updated_at=created_at + timedelta(hours=2),
    )

    async def scenario() -> None:
        path = tmp_path / "timestamps.sqlite3"
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                restored = await repository.get(track.id)
                assert restored is not None
                assert restored == track
                assert restored.created_at.tzinfo is UTC
                assert restored.created_at.hour == 10
                assert restored.updated_at.tzinfo is UTC
                assert restored.checked_at is not None
                assert restored.checked_at.tzinfo is UTC
                await repository.update(checked)
            async with sessions.begin() as session:
                repository = TrackRepository(session)
                assert await repository.get(track.id) == checked
                assert checked.updated_at == track.updated_at
                assert checked.metadata == track.metadata
                await repository.update(updated)
            async with sessions.begin() as session:
                assert await TrackRepository(session).find(track.identity) == updated

    asyncio.run(scenario())

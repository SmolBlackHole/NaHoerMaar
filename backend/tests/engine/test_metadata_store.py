# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from nahormaar_backend.engine.domain.catalog import (
    ArtistCredit,
    MediaKind,
    MediaReference,
    TrackFinding,
)
from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataProvenance,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.engine.domain.queue import QueueEntry
from nahormaar_backend.engine.domain.sessions import ListeningSession
from nahormaar_backend.engine.domain.tracks import ArtistIdentity, MediaIdentity, Track
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import (
    ArtistRepository,
    Base,
    ListeningSessionRepository,
    QueueRepository,
    TrackRepository,
)

from .database import isolated_database

TIME = datetime(2026, 9, 21, 12, tzinfo=UTC)
DISCOVERY = MetadataSource("youtube_music", MetadataKind.DISCOVERY, TIME)
DETAIL = MetadataSource("youtube", MetadataKind.DETAIL, TIME)
ARTISTS = (
    ArtistCredit(ArtistIdentity("youtube", "artist-a"), "Same name"),
    ArtistCredit(ArtistIdentity("youtube", "artist-b"), "Same name"),
)
FINDING = TrackFinding(
    MediaReference(
        MediaIdentity("youtube", "GCYGuZGE6DA"),
        MediaKind.TRACK,
        "https://music.youtube.com/watch?v=GCYGuZGE6DA",
    ),
    TrackMetadata(title="Я хочу любить", artist="Do not split, this artist"),
    ARTISTS,
)


def test_batch_shares_identity_and_artists_preserves_order_and_roundtrips_provenance(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "metadata.db"
        second = replace(
            FINDING,
            reference=replace(
                FINDING.reference, identity=MediaIdentity("youtube", "second-video")
            ),
            artists=(ARTISTS[0],),
        )
        alias = replace(
            FINDING,
            reference=replace(
                FINDING.reference,
                source_url="https://www.youtube.com/watch?v=GCYGuZGE6DA",
            ),
            metadata=TrackMetadata(duration_seconds=180),
        )
        async with isolated_database(path) as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            first, other, repeated = await store.remember(
                (FINDING, second, alias), source=DISCOVERY
            )
            assert first == repeated
            assert first.id != other.id
            assert first.source_url == FINDING.reference.source_url
            assert first.metadata == replace(FINDING.metadata, duration_seconds=180)
            assert other.artist_ids == first.artist_ids[:1]
            assert first.created_at == first.updated_at == TIME
            assert first.checked_at is None
            assert first.provenance.title == first.provenance.artists == DISCOVERY
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(first.id) == first
                assert await TrackRepository(session).get(other.id) == other
                for artist_id, expected in zip(first.artist_ids, ARTISTS, strict=True):
                    artist = await ArtistRepository(session).get(artist_id)
                    assert artist is not None
                    assert artist.identity == expected.identity
                    assert (
                        artist.name == expected.name and artist.name_source == DISCOVERY
                    )
                for table, count in {
                    "tracks": 2,
                    "artists": 2,
                    "track_artists": 3,
                    "queue_entries": 0,
                    "playback_records": 0,
                }.items():
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table]
                            )
                        )
                        == count
                    )

    asyncio.run(scenario())


def test_enrichment_and_unchanged_checks_have_separate_times_and_persist_across_reopen(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = TIME
        path = tmp_path / "metadata.db"
        detailed = replace(
            FINDING, metadata=replace(FINDING.metadata, duration_seconds=180)
        )
        async with isolated_database(path) as sessions:
            store = MetadataStore(sessions, clock=lambda: now)
            (original,) = await store.remember((FINDING,), source=DISCOVERY)
            now += timedelta(minutes=1)
            evidence = replace(
                DETAIL, observed_at=now.astimezone(timezone(timedelta(hours=2)))
            )
            (enriched,) = await store.remember((detailed,), source=evidence)
            assert enriched.created_at == original.created_at
            assert enriched.updated_at == enriched.checked_at == now
            assert enriched.id == original.id
            now += timedelta(minutes=1)
            (refreshed,) = await store.remember(
                (detailed,), source=replace(DETAIL, observed_at=now)
            )
            assert refreshed.updated_at == enriched.updated_at
            assert refreshed.checked_at == now
            assert refreshed.provenance.title != enriched.provenance.title
        async with isolated_database(path) as sessions:
            store = MetadataStore(sessions, clock=lambda: now)
            sparse = replace(
                FINDING, metadata=TrackMetadata(title="Worse title"), artists=None
            )
            (lower_quality,) = await store.remember(
                (sparse,), source=replace(DISCOVERY, observed_at=now)
            )
            assert lower_quality == refreshed
            (stale,) = await store.remember((sparse,), source=DETAIL)
            assert stale == refreshed
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(original.id) == refreshed

    asyncio.run(scenario())


def test_missing_credits_preserve_known_credits_and_explicit_empty_obeys_provenance(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            (known,) = await store.remember((FINDING,), source=DETAIL)
            future = TIME + timedelta(minutes=1)
            no_credits = replace(FINDING, metadata=TrackMetadata(), artists=None)
            (missing,) = await store.remember(
                (no_credits,), source=replace(DETAIL, observed_at=future)
            )
            assert missing.artist_ids == known.artist_ids
            assert missing.provenance.artists == DETAIL
            empty = replace(no_credits, artists=())
            (ignored,) = await store.remember(
                (empty,), source=replace(DISCOVERY, observed_at=future)
            )
            assert ignored.artist_ids == known.artist_ids
            (cleared,) = await store.remember(
                (empty,), source=replace(DETAIL, observed_at=future)
            )
            assert cleared.artist_ids == ()
            # The remembered empty observation prevents older credits from returning.
            (old,) = await store.remember((FINDING,), source=DETAIL)
            assert old.artist_ids == ()
            async with sessions.begin() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            Base.metadata.tables["artists"]
                        )
                    )
                    == 2
                )

    asyncio.run(scenario())


def test_stale_observation_of_other_track_cannot_rename_a_shared_artist(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            renamed = replace(ARTISTS[0], name="Current artist name")
            (first,) = await store.remember(
                (replace(FINDING, artists=(renamed,)),),
                source=replace(DETAIL, observed_at=TIME + timedelta(minutes=1)),
            )
            (second,) = await store.remember(
                (
                    replace(
                        FINDING,
                        reference=replace(
                            FINDING.reference,
                            identity=MediaIdentity("youtube", "other"),
                        ),
                    ),
                ),
                source=DETAIL,
            )
            assert first.artist_ids[0] == second.artist_ids[0]
            async with sessions.begin() as session:
                shared = await ArtistRepository(session).get(first.artist_ids[0])
                assert shared is not None and shared.name == renamed.name
                assert shared.name_source is not None
                assert shared.name_source.observed_at == TIME + timedelta(minutes=1)

    asyncio.run(scenario())


def test_artist_reorder_and_rename_update_shared_records_without_guessing_names(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            now = TIME
            store = MetadataStore(sessions, clock=lambda: now)
            (before,) = await store.remember((FINDING,), source=DISCOVERY)
            now += timedelta(minutes=1)
            (after,) = await store.remember(
                (
                    replace(
                        FINDING,
                        artists=(
                            replace(ARTISTS[1], name="Новое имя"),
                            ARTISTS[0],
                        ),
                    ),
                ),
                source=replace(DETAIL, observed_at=now),
            )
            assert after.artist_ids == tuple(reversed(before.artist_ids))
            assert after.updated_at == now
            assert after.metadata.artist == FINDING.metadata.artist
            async with sessions.begin() as session:
                renamed = await ArtistRepository(session).get(after.artist_ids[0])
                assert renamed is not None and renamed.name == "Новое имя"

    asyncio.run(scenario())


def test_concurrent_discoveries_share_rows_and_do_not_lose_enrichment(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            rich = replace(
                FINDING, metadata=replace(FINDING.metadata, duration_seconds=180)
            )
            results = await asyncio.gather(
                *(
                    store.remember((rich if i == 5 else FINDING,), source=DISCOVERY)
                    for i in range(10)
                )
            )
            assert len({result[0].id for result in results}) == 1
            async with sessions.begin() as session:
                track = await TrackRepository(session).find(FINDING.reference.identity)
                assert track is not None and track.metadata.duration_seconds == 180
                for table, count in {
                    "tracks": 1,
                    "artists": 2,
                    "track_artists": 2,
                }.items():
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table]
                            )
                        )
                        == count
                    )

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_batch_rolls_back_prior_enrichment_artists_and_new_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            (before,) = await store.remember(
                (replace(FINDING, artists=None),), source=DISCOVERY
            )
            original_add = TrackRepository.add
            written = asyncio.Event()
            hold = asyncio.Event()

            async def failing_add(self: TrackRepository, track: Track) -> None:
                await original_add(self, track)
                written.set()
                if cancel:
                    await hold.wait()
                raise RuntimeError("Simulated storage failure")

            enriched = replace(FINDING, metadata=TrackMetadata(title="New title"))
            second = replace(
                FINDING,
                reference=replace(
                    FINDING.reference, identity=MediaIdentity("youtube", "second")
                ),
            )
            with monkeypatch.context() as patch:
                patch.setattr(TrackRepository, "add", failing_add)
                operation = asyncio.create_task(
                    store.remember((enriched, second), source=DETAIL)
                )
                await asyncio.wait_for(written.wait(), timeout=3)
                if cancel:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
                    await asyncio.wait_for(operation, timeout=3)
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(before.id) == before
                assert (
                    await TrackRepository(session).find(second.reference.identity)
                    is None
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(
                            Base.metadata.tables["artists"]
                        )
                    )
                    == 0
                )
            # Cleanup releases both the transaction and write lock.
            (after,) = await store.remember((enriched,), source=DETAIL)
            assert after.metadata.title == "New title"

    asyncio.run(scenario())


def test_refresh_keeps_existing_queue_ids_positions_and_track_reference(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            store = MetadataStore(sessions, clock=lambda: TIME)
            (track,) = await store.remember((FINDING,), source=DISCOVERY)
            owner = ListeningSession()
            entry = QueueEntry(owner.id, track.id, 0)
            async with sessions.begin() as session:
                await ListeningSessionRepository(session).add(owner)
                await QueueRepository(session).add(entry)
            (updated,) = await store.remember(
                (replace(FINDING, metadata=TrackMetadata(title="Detailed title")),),
                source=DETAIL,
            )
            assert updated.id == entry.track_id
            async with sessions.begin() as session:
                assert await QueueRepository(session).entries(owner.id) == (entry,)

    asyncio.run(scenario())


def test_imported_values_without_provenance_survive_search_and_empty_batch_does_no_work(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "metadata.db") as sessions:
            track = Track(
                FINDING.reference.identity,
                FINDING.reference.source_url,
                TrackMetadata(title="Imported"),
                created_at=TIME,
                updated_at=TIME,
            )
            async with sessions.begin() as session:
                await TrackRepository(session).add(track)
            store = MetadataStore(sessions, clock=lambda: TIME)
            sparse = replace(
                FINDING, metadata=TrackMetadata(title="Search"), artists=None
            )
            (remembered,) = await store.remember((sparse,), source=DISCOVERY)
            assert remembered == track and remembered.provenance == MetadataProvenance()
        # Even a disposed database and an invalid clock are irrelevant for no input.
        empty = MetadataStore(sessions, clock=lambda: TIME.replace(tzinfo=None))
        assert await empty.remember((), source=DETAIL) == ()

    asyncio.run(scenario())

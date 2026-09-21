# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, SessionTransaction

from nahormaar_backend.engine.audio import PlayableSource
from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.domain.catalog import (
    ArtistCredit,
    MediaKind,
    MediaReference,
    TrackFinding,
)
from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataSource,
    TrackMetadata,
)
from nahormaar_backend.engine.domain.tracks import ArtistIdentity, MediaIdentity
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import Base, TrackRepository
from nahormaar_backend.engine.providers import ProviderError, UnsupportedCapability
from nahormaar_backend.engine.youtube import YouTubeProvider
from nahormaar_backend.integrations.processes import ProcessResult

from .database import isolated_database
from .test_youtube_provider import IDENTITY, URL, FixtureRunner, details

TIME = datetime(2026, 9, 21, 12, tzinfo=UTC)
REFERENCE = MediaReference(IDENTITY, MediaKind.TRACK, URL)


class DetailsOnly:
    def __init__(self, key: str, finding: TrackFinding) -> None:
        self.key = key
        self.finding = finding
        self.calls = 0

    def identify(
        self, source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        return REFERENCE if source_url == URL else None

    async def track(self, identity: MediaIdentity) -> TrackFinding:
        self.calls += 1
        assert identity == IDENTITY
        return self.finding

    async def close(self) -> None:
        pass


def test_catalog_ingests_details_from_aliases_without_persisting_audio_secrets(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "catalog.sqlite3"
        now = TIME
        async with isolated_database(path) as sessions:
            metadata = MetadataStore(sessions, clock=lambda: now)
            artist = ArtistCredit(ArtistIdentity("youtube", "UCArtist"), "Known artist")
            (known,) = await metadata.remember(
                (TrackFinding(REFERENCE, TrackMetadata(title="Discovery"), (artist,)),),
                source=MetadataSource("youtube_music", MetadataKind.DISCOVERY, TIME),
            )
            runner = FixtureRunner(details())
            provider = YouTubeProvider(Path("node"), runner=runner)
            catalog = Catalog((provider,), metadata, clock=lambda: now)
            now += timedelta(seconds=1)
            first = await catalog.track(
                "https://music.youtube.com/watch?v=GCYGuZGE6DA&list=PLfixture012345"
            )
            assert first.id == known.id and first.artist_ids == known.artist_ids
            assert first.metadata.title == "Я хочу любить"
            assert first.checked_at == now
            assert first.provenance.title == MetadataSource(
                "youtube", MetadataKind.DETAIL, now
            )
            now += timedelta(seconds=1)
            runner.result = details(title=None, duration=181)
            playable = await catalog.resolve_audio("https://youtu.be/GCYGuZGE6DA")
            assert (
                len(runner.calls) == 2
            )  # Audio contributes metadata in the same request.
            assert "temporary-secret" in playable.stream_url
            async with sessions.begin() as session:
                stored = await TrackRepository(session).get(first.id)
                assert stored is not None
                assert stored.metadata.title == first.metadata.title
                assert stored.metadata.duration_seconds == 181
                assert stored.checked_at == now
                assert stored.artist_ids == known.artist_ids
                for table in Base.metadata.tables.values():
                    rows = (await session.execute(select(table))).all()
                    assert "temporary-secret" not in repr(rows)
                    assert "ephemeral-header" not in repr(rows)
                for table_name, count in {
                    "tracks": 1,
                    "artists": 1,
                    "queue_entries": 0,
                    "playback_records": 0,
                }.items():
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(
                                Base.metadata.tables[table_name]
                            )
                        )
                        == count
                    )
            await provider.close()
        async with isolated_database(path) as sessions:
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(known.id) == stored

    asyncio.run(scenario())


def test_catalog_routes_by_registration_order_or_explicit_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.sqlite3") as sessions:
            first = DetailsOnly(
                "youtube_music",
                TrackFinding(REFERENCE, TrackMetadata(title="Music result")),
            )
            second = DetailsOnly(
                "youtube", TrackFinding(REFERENCE, TrackMetadata(duration_seconds=180))
            )
            metadata = MetadataStore(sessions, clock=lambda: TIME)
            catalog = Catalog((first, second), metadata, clock=lambda: TIME)
            music = await catalog.track(URL)
            assert music.metadata.title == "Music result"
            assert first.calls == 1 and second.calls == 0
            youtube = await catalog.track(URL, provider_key="youtube")
            assert youtube.id == music.id
            assert youtube.metadata.title == music.metadata.title
            assert youtube.metadata.duration_seconds == 180
            assert youtube.provenance.title is not None
            assert youtube.provenance.title.provider == "youtube_music"
            assert youtube.provenance.duration_seconds is not None
            assert youtube.provenance.duration_seconds.provider == "youtube"
            with pytest.raises(ValueError, match="unique"):
                Catalog((first, first), metadata, clock=lambda: TIME)
            with pytest.raises(UnsupportedCapability, match="Unknown"):
                await catalog.track(URL, provider_key="spotify")
            with pytest.raises(UnsupportedCapability, match="resolve audio"):
                await catalog.resolve_audio(URL, provider_key="youtube_music")
            assert first.calls == second.calls == 1

    asyncio.run(scenario())


def test_link_and_capability_errors_do_not_fall_back_or_touch_storage(
    tmp_path: Path,
) -> None:
    class IdentifiesOnly:
        key = "unavailable"

        def identify(
            self, source_url: str, *, kind: MediaKind | None = None
        ) -> MediaReference | None:
            return REFERENCE if source_url == URL else None

        async def close(self) -> None:
            pass

    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.sqlite3") as sessions:
            runner = FixtureRunner(details())
            provider = YouTubeProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (IdentifiesOnly(), provider),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            for explicit in (None, "unavailable"):
                with pytest.raises(UnsupportedCapability, match="track details"):
                    await catalog.track(URL, provider_key=explicit)
            for url in (
                "https://music.youtube.com/watch?v=GCYGuZGE6DA",
                "https://example.com/track",
            ):
                with pytest.raises(UnsupportedCapability, match="not supported"):
                    await catalog.track(url, provider_key="unavailable")
            with pytest.raises(UnsupportedCapability, match="single track"):
                await catalog.track("https://youtube.com/playlist?list=PLfixture012345")
            assert runner.calls == []
            async with sessions.begin() as session:
                assert await TrackRepository(session).find(IDENTITY) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("resolve_audio", [False, True])
def test_provider_identity_mismatch_is_not_persisted(
    tmp_path: Path, resolve_audio: bool
) -> None:
    class WrongAudio(DetailsOnly):
        async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource:
            return PlayableSource(self.finding, "https://stream.example/audio")

    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.sqlite3") as sessions:
            wrong = replace(REFERENCE, identity=MediaIdentity("youtube", "other-video"))
            provider = WrongAudio(
                "youtube_music", TrackFinding(wrong, TrackMetadata(title="Wrong track"))
            )
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            with pytest.raises(ProviderError, match="different track identity"):
                if resolve_audio:
                    await catalog.resolve_audio(URL)
                else:
                    await catalog.track(URL)
            async with sessions.begin() as session:
                assert await TrackRepository(session).find(IDENTITY) is None
                assert await TrackRepository(session).find(wrong.identity) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("resolve_audio", [False, True])
def test_failed_or_cancelled_provider_work_opens_no_metadata_transaction(
    tmp_path: Path, resolve_audio: bool
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.sqlite3") as sessions:
            started = asyncio.Event()
            transactions: list[SessionTransaction] = []

            def transaction_created(
                session: Session, transaction: SessionTransaction
            ) -> None:
                transactions.append(transaction)

            async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
                started.set()
                await asyncio.Event().wait()
                return details()

            provider = YouTubeProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            event.listen(Session, "after_transaction_create", transaction_created)
            try:
                task = asyncio.create_task(
                    catalog.resolve_audio(URL) if resolve_audio else catalog.track(URL)
                )
                await started.wait()
                assert transactions == []
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert transactions == []
                failed = YouTubeProvider(
                    Path("node"),
                    runner=FixtureRunner(ProcessResult(1, b"", b"Private video")),
                )
                failing_catalog = Catalog(
                    (failed,),
                    MetadataStore(sessions, clock=lambda: TIME),
                    clock=lambda: TIME,
                )
                with pytest.raises(ProviderError):
                    if resolve_audio:
                        await failing_catalog.resolve_audio(URL)
                    else:
                        await failing_catalog.track(URL)
                assert transactions == []
                await catalog.close()
                await failing_catalog.close()
                await provider.close()
                await failed.close()

                async def successful_runner(
                    args: tuple[str, ...], *, timeout: float
                ) -> ProcessResult:
                    assert transactions == []
                    return details()

                successful = YouTubeProvider(Path("node"), runner=successful_runner)
                successful_catalog = Catalog(
                    (successful,),
                    MetadataStore(sessions, clock=lambda: TIME),
                    clock=lambda: TIME,
                )
                if resolve_audio:
                    await successful_catalog.resolve_audio(URL)
                else:
                    await successful_catalog.track(URL)
                assert transactions  # Storage starts only after the provider returns.
                await successful.close()
            finally:
                event.remove(Session, "after_transaction_create", transaction_created)

    asyncio.run(scenario())


def test_slow_older_request_cannot_overwrite_a_newer_observation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "catalog.sqlite3") as sessions:
            now = TIME
            started, release = asyncio.Event(), asyncio.Event()
            calls = 0

            async def runner(args: tuple[str, ...], *, timeout: float) -> ProcessResult:
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await release.wait()
                    return details(title="Older title")
                return details(title="Newer title")

            provider = YouTubeProvider(Path("node"), runner=runner)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: now),
                clock=lambda: now,
            )
            slow = asyncio.create_task(catalog.track(URL))
            await started.wait()
            now += timedelta(seconds=10)
            latest = await catalog.resolve_audio(URL)
            assert latest.track.metadata.title == "Newer title"
            now += timedelta(seconds=10)
            release.set()
            completed = await slow
            assert completed.metadata.title == "Newer title"
            assert completed.checked_at == TIME + timedelta(seconds=10)
            assert completed.provenance.title is not None
            assert completed.provenance.title.observed_at == completed.checked_at
            async with sessions.begin() as session:
                assert await TrackRepository(session).get(completed.id) == completed
            await provider.close()

    asyncio.run(scenario())

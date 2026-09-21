# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.catalog import (
    CatalogEntries,
    MediaCatalog,
    source_key,
)
from nahormaar_backend.application.radio import RadioCatalog
from nahormaar_backend.domain.catalog import CatalogTrack, SearchSource
from nahormaar_backend.domain.models import (
    Contributor,
    HistoryEntry,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
)
from nahormaar_backend.domain.radio import RadioSeed
from nahormaar_backend.integrations import catalog as factory
from nahormaar_backend.integrations.processes import ProcessResult
from nahormaar_backend.integrations.youtube import video_id
from test_search import Provider

VIDEO = "https://www.youtube.com/watch?v=Pqp9fDRp1lw"
MUSIC = "https://music.youtube.com/watch?v=Pqp9fDRp1lw"


class Extractor:
    def __init__(self) -> None:
        self.stopped = False
        self.closes = 0

    async def run(
        self,
        source: str,
        options: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        raise AssertionError("No discovery process should be requested.")

    async def execute(
        self,
        args: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        raise AssertionError("No discovery process should be requested.")

    def stop_accepting(self) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.stopped = True
        self.closes += 1


class Tracks:
    def __init__(self, *entries: CatalogTrack) -> None:
        self.entries = entries

    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]:
        return self.entries[:limit]

    async def recommend(self, seed: RadioSeed, limit: int) -> tuple[CatalogTrack, ...]:
        return self.entries[:limit]


def test_catalog_owns_injected_services_and_cancels_search_once() -> None:
    async def scenario() -> None:
        extractor, provider = Extractor(), Provider()
        provider.release = asyncio.Event()
        catalog = MediaCatalog(extractor, {SearchSource.MUSIC: provider})
        pending = asyncio.create_task(catalog.search("song"))
        await provider.started.wait()
        await catalog.close()
        await catalog.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert provider.cancelled
        assert extractor.stopped and extractor.closes == 1

    asyncio.run(scenario())


def test_factory_cleans_extractor_on_composition_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        extractor = Extractor()

        def create_extractor(path: Path) -> Extractor:
            return extractor

        monkeypatch.setattr(factory, "DiscoveryExtractor", create_extractor)

        def fail(*args: object) -> None:
            raise RuntimeError("Provider construction failed")

        monkeypatch.setattr(factory, "YouTubeMusicSearch", fail)
        with pytest.raises(RuntimeError, match="Provider construction failed"):
            await factory.create_media_catalog(Path("node"))
        assert extractor.closes == 1

    asyncio.run(scenario())


def test_factory_transfers_extractor_ownership_to_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        extractor = Extractor()

        def create_extractor(path: Path) -> Extractor:
            return extractor

        monkeypatch.setattr(factory, "DiscoveryExtractor", create_extractor)
        catalog = await factory.create_media_catalog(Path("node"))
        assert extractor.closes == 0
        await catalog.close()
        await catalog.close()
        assert extractor.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source", [VIDEO, MUSIC, "https://youtu.be/Pqp9fDRp1lw?list=PL12345678901234"]
)
def test_provider_identity_matches_supported_url_variants(source: str) -> None:
    assert video_id(source) == source_key(source) == "Pqp9fDRp1lw"


def test_identity_preserves_unknown_sources_without_aliasing() -> None:
    source = "https://example.org/music/song"
    assert video_id(source) is None
    assert source_key(source) == source


def test_catalog_entry_preserves_metadata_precedence_and_request_identity() -> None:
    async def scenario() -> None:
        extractor = Extractor()
        catalog = MediaCatalog(
            extractor,
            {
                SearchSource.MUSIC: Tracks(
                    CatalogTrack(
                        source_url=VIDEO,
                        video_id="Pqp9fDRp1lw",
                        title="Search title",
                        artist="Search artist",
                        duration_seconds=180,
                    )
                )
            },
        )
        radio = RadioCatalog(
            Tracks(
                CatalogTrack(
                    source_url=MUSIC,
                    video_id="Pqp9fDRp1lw",
                    title="Radio title",
                )
            )
        )
        original_actor = Contributor(uuid4(), "Previous listener", "0000")
        new_actor = Contributor(uuid4(), "New listener", "0000")
        current = QueueEntry(
            MUSIC,
            title="Current title",
            duration_seconds=200,
            uploader="Known uploader",
            added_by=original_actor,
            origin="radio",
        )
        snapshot = PlayerSnapshot(state=PlaybackState.PLAYING, current=current)
        entries = CatalogEntries(
            catalog=catalog, radio=radio, snapshot=lambda: snapshot
        )
        try:
            await catalog.search("song")
            await radio.preview(
                uuid4(), new_actor.id, RadioSeed("track", "Pqp9fDRp1lw", "Seed")
            )
            added = entries.queue_entry(VIDEO, new_actor)
            assert added.title == "Radio title"
            assert added.artist == "Search artist"
            assert added.duration_seconds == 180
            assert added.uploader == "Known uploader"
            assert added.source_url == VIDEO
            assert added.id != current.id and added.added_by == new_actor
            assert added.origin == "manual"
        finally:
            await radio.close()
            await catalog.close()

    asyncio.run(scenario())


def test_partial_search_metadata_preserves_known_fields_in_cache() -> None:
    async def scenario() -> None:
        provider = Tracks(
            CatalogTrack(
                source_url=VIDEO,
                video_id="Pqp9fDRp1lw",
                title="Original",
                artist="Known artist",
                duration_seconds=180,
            )
        )
        catalog = MediaCatalog(Extractor(), {SearchSource.MUSIC: provider})
        try:
            await catalog.search("first query")
            original = catalog.cached_metadata(VIDEO)
            provider.entries = (
                CatalogTrack(
                    source_url=MUSIC,
                    video_id="Pqp9fDRp1lw",
                    title="Updated",
                    thumbnail_url="cover",
                ),
            )
            await catalog.search("second query")
            assert catalog.cached_metadata(MUSIC) == replace(
                original, title="Updated", thumbnail_url="cover"
            )
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_entry_metadata_reads_current_snapshot_and_history_without_io() -> None:
    history = QueueEntry(VIDEO, title="Remembered", duration_seconds=150)
    snapshot = PlayerSnapshot(
        recently_played=(HistoryEntry(history, datetime.now(UTC)),)
    )
    entries = CatalogEntries(catalog=None, radio=None, snapshot=lambda: snapshot)
    first = entries.queue_entry(MUSIC, None)
    assert first.title == "Remembered" and first.duration_seconds == 150
    assert first.id != history.id

    current = QueueEntry(MUSIC, title="Current", duration_seconds=160)
    snapshot = PlayerSnapshot(state=PlaybackState.PLAYING, current=current)
    second = entries.queue_entry(VIDEO, None)
    assert second.title == "Current" and second.duration_seconds == 160

    unknown = entries.queue_entry("https://example.org/song", None)
    assert unknown.title is None and unknown.video_id is None


def test_metadata_fallback_stops_at_first_complete_match() -> None:
    current = QueueEntry(VIDEO, title="Current")
    upcoming = QueueEntry(MUSIC, duration_seconds=123)
    older = QueueEntry(VIDEO, artist="Older artist")
    snapshot = PlayerSnapshot(
        state=PlaybackState.PLAYING,
        current=current,
        upcoming=(upcoming,),
        recently_played=(HistoryEntry(older, datetime.now(UTC)),),
    )
    entries = CatalogEntries(catalog=None, radio=None, snapshot=lambda: snapshot)
    entry = entries.queue_entry(MUSIC, None)
    assert entry.title == "Current" and entry.duration_seconds == 123
    assert entry.artist is None

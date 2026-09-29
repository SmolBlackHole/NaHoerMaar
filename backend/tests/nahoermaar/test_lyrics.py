# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os

from nahoermaar.catalog.domain import ObservationQuality, ProviderName
from nahoermaar.catalog.providers import ProviderArtist, ProviderTrack
from nahoermaar.catalog.repository import CatalogRepository
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.core import Database
from nahoermaar.database.schema import migrate
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.lyrics.domain import LyricsState, TrackLyrics, parse_synced_lyrics
from nahoermaar.lyrics.providers import LyricsProviderError, ProviderLyrics
from nahoermaar.lyrics.repository import LyricsRepository
from nahoermaar.lyrics.service import LyricsService

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


class Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.result: ProviderLyrics | None = ProviderLyrics(
            42,
            "Still Alive",
            "Mt Eden",
            "Still Alive",
            252,
            False,
            "First line\nSecond line",
            "[00:01.00]First line\n[00:03.50]Second line",
        )
        self.failure: LyricsProviderError | None = None

    async def get(
        self,
        *,
        track_name: str,
        artist_name: str,
        album_name: str | None,
        duration_seconds: float | None,
    ) -> ProviderLyrics | None:
        assert (track_name, artist_name, album_name, duration_seconds) == (
            "Still Alive",
            "Mt Eden",
            "Still Alive",
            None,
        )
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.result


def test_synced_lyrics_are_ordered_and_support_multiple_timestamps() -> None:
    lines = parse_synced_lyrics(
        "[00:03.50]Second\n[00:01.00][00:02.00]First\n[ar:Artist]",
        5,
    )
    assert [(line.start_seconds, line.end_seconds, line.text) for line in lines] == [
        (1.0, 2.0, "First"),
        (2.0, 3.5, "First"),
        (3.5, 5, "Second"),
    ]


def test_lyrics_cache_handles_hits_expiry_misses_and_stale_fallback() -> None:
    database = Database(os.environ["DATABASE_URL"])
    clock = [NOW]
    provider = Provider()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    async def scenario() -> None:
        await migrate(database.engine)
        observation = ProviderTrack(
            ProviderName.YOUTUBE_MUSIC,
            "still-alive",
            "https://music.youtube.com/watch?v=still-alive",
            "Still Alive",
            "Mt Eden",
            (
                ProviderArtist(
                    ProviderName.YOUTUBE_MUSIC,
                    "mt-eden",
                    "Mt Eden",
                ),
            ),
            252,
            album_title="Still Alive",
            quality=ObservationQuality.DETAIL,
        )
        async with units() as work:
            track = await CatalogRepository(work.session).upsert(observation, NOW)
            await work.commit()
        legacy_payload = {
            "title": track.title.casefold(),
            "artists": [
                credit.artist.name.casefold()
                for credit in sorted(track.artists, key=lambda item: item.position)
            ],
            "album": track.album_title.casefold() if track.album_title else None,
            "duration": (
                round(track.duration_seconds)
                if track.duration_seconds is not None
                else None
            ),
        }
        legacy_signature = hashlib.sha256(
            json.dumps(
                legacy_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with units() as work:
            await LyricsRepository(work.session).save(
                TrackLyrics(
                    track.id,
                    legacy_signature,
                    LyricsState.AVAILABLE,
                    99,
                    "Old translated line",
                    "[00:01.00]Old translated line",
                    NOW,
                    NOW + timedelta(hours=1),
                )
            )
            await work.commit()
        catalog = CatalogService(units, (), clock=lambda: clock[0])
        service = LyricsService(
            units,
            catalog,
            provider,
            clock=lambda: clock[0],
            success_ttl=timedelta(hours=1),
            miss_ttl=timedelta(minutes=10),
        )

        first = await service.get(track.id)
        again = await service.get(track.id)
        assert first.lyrics.state is LyricsState.AVAILABLE
        assert not first.cached
        assert first.lyrics.provider_record_id == 42
        assert first.lyrics.metadata_signature != legacy_signature
        assert again.cached
        assert provider.calls == 1

        clock[0] += timedelta(hours=2)
        provider.failure = LyricsProviderError("offline")
        stale = await service.get(track.id)
        assert stale.cached and stale.stale
        assert stale.lyrics.provider_record_id == 42

        provider.failure = None
        provider.result = None
        missing = await service.get(track.id, refresh=True)
        missing_again = await service.get(track.id)
        assert missing.lyrics.state is LyricsState.NOT_FOUND
        assert missing_again.cached
        assert provider.calls == 3

        await catalog.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

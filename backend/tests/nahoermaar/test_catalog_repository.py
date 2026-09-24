# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, func, select

from nahoermaar.catalog.domain import (
    DiscoveryKind,
    DiscoverySnapshot,
    ObservationQuality,
    ProviderName,
    SourceAvailability,
)
from nahoermaar.catalog.providers import ProviderArtist, ProviderTrack
from nahoermaar.catalog.repository import CatalogRepository, DiscoveryRepository
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def _track(**changes: object) -> ProviderTrack:
    values: dict[str, object] = {
        "provider": ProviderName.YOUTUBE,
        "external_id": "abcdefghijk",
        "source_url": "https://music.youtube.com/watch?v=abcdefghijk",
        "title": "Discovery title",
        "artist_text": "Known artist",
        "artists": (
            ProviderArtist(ProviderName.YOUTUBE_MUSIC, "UCknown", "Known artist"),
        ),
        "duration_seconds": 180.0,
        "artwork_url": "https://img.example/cover.jpg",
        "isrc": "DEABC2600001",
    }
    values.update(changes)
    return ProviderTrack(**values)  # type: ignore[arg-type]


def test_catalog_upserts_provider_identity_and_recomputes_preferred_metadata() -> None:
    database = _database()

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            repository = CatalogRepository(work.session)
            discovered = await repository.upsert(_track(), NOW)
            await work.commit()
        detail = replace(
            _track(),
            title="Detailed title",
            duration_seconds=181.5,
            quality=ObservationQuality.DETAIL,
        )
        async with UnitOfWork(database.sessions) as work:
            repository = CatalogRepository(work.session)
            stored = await repository.upsert(detail, NOW + timedelta(minutes=1))
            older_discovery = await repository.upsert(
                replace(_track(), title="Stale discovery"),
                NOW + timedelta(minutes=2),
            )
            await work.commit()
        assert discovered.id == stored.id == older_discovery.id
        assert stored.title == older_discovery.title == "Detailed title"
        assert stored.duration_seconds == 181.5
        assert len(stored.sources) == 1
        assert len(stored.artists) == 1
        assert stored.artists[0].artist.name == "Known artist"

        async with UnitOfWork(database.sessions) as work:
            for table_name in (
                "artists",
                "artist_sources",
                "tracks",
                "track_sources",
            ):
                count = await work.session.scalar(
                    select(func.count()).select_from(Base.metadata.tables[table_name])
                )
                assert count == 1
            for table_name in (
                "artists",
                "artist_sources",
                "tracks",
                "track_sources",
                "track_artists",
                "track_source_artists",
            ):
                assert all(
                    not isinstance(column.type, JSON)
                    for column in Base.metadata.tables[table_name].columns
                )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_discovery_snapshots_reference_sources_and_keep_three_versions() -> None:
    database = _database()

    async def scenario() -> None:
        versions: list[DiscoverySnapshot] = []
        async with UnitOfWork(database.sessions) as work:
            catalog = CatalogRepository(work.session)
            track = await catalog.upsert(_track(), NOW)
            source_id = track.sources[0].id
            discovery = DiscoveryRepository(work.session)
            for offset in range(5):
                fetched = NOW + timedelta(minutes=offset)
                versions.append(
                    await discovery.publish(
                        kind=DiscoveryKind.SEARCH,
                        provider_key="youtube_music",
                        locator="known artist",
                        limit=50,
                        source_url=None,
                        playlist_title=None,
                        source_ids=(source_id, source_id),
                        fetched_at=fetched,
                        expires_at=fetched + timedelta(minutes=5),
                        source_has_more=False,
                        continuation=None,
                    )
                )
            await work.commit()
        async with UnitOfWork(database.sessions) as work:
            latest = await DiscoveryRepository(work.session).latest(
                DiscoveryKind.SEARCH,
                "youtube_music",
                "known artist",
                50,
            )
            assert latest is not None
            assert latest.id == versions[-1].id
            assert [entry.position for entry in latest.entries] == [0, 1]
            assert latest.entries[0].source.id == latest.entries[1].source.id
            snapshot_count = await work.session.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["discovery_snapshots"]
                )
            )
            source_count = await work.session.scalar(
                select(func.count()).select_from(Base.metadata.tables["track_sources"])
            )
            assert snapshot_count == 3
            assert source_count == 1

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_source_availability_and_orphan_pruning_preserve_referenced_tracks() -> None:
    database = _database()

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            catalog = CatalogRepository(work.session)
            pinned = await catalog.upsert(_track(), NOW)
            orphan = await catalog.upsert(
                _track(external_id="orphanvideo", isrc="DEABC2600002"),
                NOW,
            )
            await DiscoveryRepository(work.session).publish(
                kind=DiscoveryKind.SEARCH,
                provider_key="youtube_music",
                locator="known artist",
                limit=50,
                source_url=None,
                playlist_title=None,
                source_ids=(pinned.sources[0].id,),
                fetched_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
                source_has_more=False,
                continuation=None,
            )
            assert not await catalog.mark_unavailable(
                ProviderName.YOUTUBE,
                pinned.sources[0].external_id,
                NOW - timedelta(seconds=1),
            )
            assert await catalog.mark_unavailable(
                ProviderName.YOUTUBE,
                pinned.sources[0].external_id,
                NOW + timedelta(minutes=5),
            )
            removed = await catalog.prune_orphans(NOW + timedelta(minutes=30))
            await work.commit()

        assert removed == (1, 1, 0)
        async with UnitOfWork(database.sessions) as work:
            catalog = CatalogRepository(work.session)
            stored = await catalog.by_source(
                ProviderName.YOUTUBE, pinned.sources[0].external_id
            )
            assert stored is not None
            assert stored.sources[0].availability is SourceAvailability.UNAVAILABLE
            assert (
                await catalog.by_source(
                    ProviderName.YOUTUBE, orphan.sources[0].external_id
                )
                is None
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

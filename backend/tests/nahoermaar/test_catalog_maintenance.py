# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import insert

from nahoermaar.catalog.domain import ProviderName
from nahoermaar.catalog.maintenance import CatalogCleanup
from nahoermaar.catalog.providers import ProviderArtist, ProviderTrack
from nahoermaar.catalog.repository import CatalogRepository
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.operations.scheduler import (
    JobExecution,
    JobOptions,
    JobProgress,
    JobProgressUnit,
)
from nahoermaar.operations.jobs import JobTrigger
from nahoermaar.users.domain import UserId
from nahoermaar.views.catalog import CatalogCleanupView

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def _track(external_id: str, title: str, artist_id: str, isrc: str) -> ProviderTrack:
    return ProviderTrack(
        provider=ProviderName.YOUTUBE,
        external_id=external_id,
        source_url=f"https://music.youtube.com/watch?v={external_id}",
        title=title,
        artist_text=f"Artist {artist_id}",
        artists=(
            ProviderArtist(
                ProviderName.YOUTUBE_MUSIC,
                artist_id,
                f"Artist {artist_id}",
            ),
        ),
        duration_seconds=180.0,
        isrc=isrc,
    )


def test_catalog_cleanup_preview_matches_execution_and_preserves_playback() -> None:
    database = _database()

    async def scenario() -> None:
        observed_at = NOW - timedelta(days=120)
        async with UnitOfWork(database.sessions) as work:
            repository = CatalogRepository(work.session)
            orphan = await repository.upsert(
                _track("orphan00001", "Unused track", "orphan", "DEABC2600101"),
                observed_at,
            )
            protected = await repository.upsert(
                _track(
                    "history0001",
                    "Played track",
                    "history",
                    "DEABC2600102",
                ),
                observed_at,
            )
            owner_id = UserId(uuid4())
            session_id = uuid4()
            request_id = uuid4()
            await work.session.execute(
                insert(Base.metadata.tables["users"]).values(
                    id=owner_id,
                    role="owner",
                    access_granted_by=None,
                    access_granted_at=None,
                    created_at=NOW,
                    updated_at=NOW,
                    last_login_at=None,
                )
            )
            await work.session.execute(
                insert(Base.metadata.tables["listening_sessions"]).values(
                    id=session_id,
                    session_key="default",
                    revision=0,
                    queue_revision=0,
                    channel_id=None,
                    volume=1.0,
                    crossfade_seconds=7,
                    sleep_at=None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            await work.session.execute(
                insert(Base.metadata.tables["track_requests"]).values(
                    id=request_id,
                    session_id=session_id,
                    track_id=protected.id,
                    source_id=protected.sources[0].id,
                    requested_at=NOW,
                    origin="manual",
                    requested_by=owner_id,
                    radio_run_id=None,
                )
            )
            await work.session.execute(
                insert(Base.metadata.tables["playback_records"]).values(
                    id=uuid4(),
                    session_id=session_id,
                    request_id=request_id,
                    started_at=NOW,
                    audio_seconds=30.0,
                    group_audio_seconds=30.0,
                    ended_at=NOW + timedelta(seconds=30),
                    end_reason="completed",
                )
            )
            await work.commit()

        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        progress: list[JobProgress] = []

        def report(current: int, total: int, unit: JobProgressUnit) -> None:
            progress.append(JobProgress(current, total, unit))

        cleanup = CatalogCleanup(
            units,
            CatalogCleanupView(),
            clock=lambda: NOW,
        )
        preview = await cleanup.run(
            JobExecution(
                JobOptions(batch_size=10, preview=True, age_days=90),
                JobTrigger.MANUAL,
                owner_id,
                report,
            )
        )
        assert preview.candidate_count == 1
        assert preview.processed_count == 1
        assert preview.changed_count == 0
        assert {detail.subject_id for detail in preview.details} == {
            str(orphan.sources[0].id),
            str(orphan.id),
            str(orphan.artists[0].artist.id),
        }

        async with UnitOfWork(database.sessions) as work:
            repository = CatalogRepository(work.session)
            assert (
                await repository.by_source(
                    ProviderName.YOUTUBE,
                    orphan.sources[0].external_id,
                )
                is not None
            )

        executed = await cleanup.run(
            JobExecution(
                JobOptions(batch_size=10, preview=False, age_days=90),
                JobTrigger.MANUAL,
                owner_id,
                report,
            )
        )
        assert executed.candidate_count == 1
        assert executed.processed_count == 1
        assert executed.changed_count == 3
        assert {detail.subject_id for detail in executed.details} == {
            detail.subject_id for detail in preview.details
        }
        assert progress[-1] == JobProgress(1, 1, JobProgressUnit.RECORDS)

        async with UnitOfWork(database.sessions) as work:
            repository = CatalogRepository(work.session)
            assert (
                await repository.by_source(
                    ProviderName.YOUTUBE,
                    orphan.sources[0].external_id,
                )
                is None
            )
            assert (
                await repository.by_source(
                    ProviderName.YOUTUBE,
                    protected.sources[0].external_id,
                )
                is not None
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

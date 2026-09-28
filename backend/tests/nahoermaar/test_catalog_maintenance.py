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

from nahoermaar.catalog.domain import (
    MediaKind,
    MediaReference,
    ProviderName,
    SourceAvailability,
)
from nahoermaar.catalog.maintenance import CatalogCleanup, SourceRevalidation
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
from nahoermaar.operations.scheduler import (
    JobExecution,
    JobOptions,
    JobProgress,
    JobProgressUnit,
)
from nahoermaar.operations.jobs import JobRunDetailOutcome, JobTrigger
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


class _RevalidationProvider:
    key = "youtube"

    def __init__(self) -> None:
        self.fail = True
        self.track_calls = 0

    def identify(
        self,
        source_url: str,
        *,
        kind: MediaKind | None = None,
    ) -> MediaReference | None:
        if source_url != "https://music.youtube.com/watch?v=retry000001":
            return None
        return MediaReference(
            ProviderName.YOUTUBE,
            "retry000001",
            kind or MediaKind.TRACK,
            source_url,
        )

    async def track(self, reference: MediaReference) -> ProviderTrack:
        self.track_calls += 1
        if self.fail:
            raise ProviderError("Still unavailable.", retryable=True)
        return ProviderTrack(
            provider=ProviderName.YOUTUBE,
            external_id=reference.external_id,
            source_url=reference.source_url,
            title="Recovered track",
            artist_text="Recovered artist",
            duration_seconds=181.0,
        )

    async def search(
        self,
        query: str,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        raise AssertionError("Search is not used by source revalidation.")

    async def playlist(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPlaylist:
        raise AssertionError("Playlist is not used by source revalidation.")

    async def radio(
        self,
        reference: MediaReference,
        *,
        limit: int,
        continuation: str | None = None,
    ) -> ProviderPage:
        raise AssertionError("Radio is not used by source revalidation.")

    async def resolve_audio(self, reference: MediaReference) -> ProviderAudio:
        raise AssertionError("Audio is not used by source revalidation.")

    async def close(self) -> None:
        return None


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


def test_source_revalidation_persists_backoff_and_restores_availability() -> None:
    database = _database()
    now = [NOW]
    provider = _RevalidationProvider()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = CatalogService(
        units,
        (provider,),
        clock=lambda: now[0],
    )
    revalidation = SourceRevalidation(
        units,
        service,
        clock=lambda: now[0],
        delay=0,
    )
    progress: list[JobProgress] = []

    def report(current: int, total: int, unit: JobProgressUnit) -> None:
        progress.append(JobProgress(current, total, unit))

    execution = JobExecution(
        JobOptions(batch_size=10),
        JobTrigger.MANUAL,
        None,
        report,
    )

    async def scenario() -> None:
        async with units() as work:
            stored = await CatalogRepository(work.session).upsert(
                ProviderTrack(
                    provider=ProviderName.YOUTUBE,
                    external_id="retry000001",
                    source_url="https://music.youtube.com/watch?v=retry000001",
                    title="Unavailable track",
                    availability=SourceAvailability.UNAVAILABLE,
                ),
                NOW - timedelta(days=1),
            )
            source_id = stored.sources[0].id
            await work.commit()

        failed = await revalidation.run(execution)
        assert failed.candidate_count == 1
        assert failed.processed_count == 1
        assert failed.changed_count == 0
        assert failed.failure_count == 1
        assert failed.error_code == "source_revalidation_partial"
        assert failed.details[0].subject_id == str(source_id)
        assert failed.details[0].source == "youtube"
        assert failed.details[0].outcome is JobRunDetailOutcome.FAILED

        async with units() as work:
            source = await CatalogRepository(work.session).source(source_id)
            assert source is not None
            assert source.availability is SourceAvailability.UNAVAILABLE
            assert source.failure_count == 1
            assert source.retry_at == NOW + timedelta(hours=1)
            assert source.last_failure_code == "provider_failed"

        waiting = await revalidation.run(execution)
        assert waiting.candidate_count == 0
        assert provider.track_calls == 1

        now[0] += timedelta(hours=1)
        failed_again = await revalidation.run(execution)
        assert failed_again.candidate_count == 1
        assert failed_again.failure_count == 1
        assert provider.track_calls == 2

        async with units() as work:
            source = await CatalogRepository(work.session).source(source_id)
            assert source is not None
            assert source.failure_count == 2
            assert source.retry_at == now[0] + timedelta(hours=2)

        waiting_again = await revalidation.run(execution)
        assert waiting_again.candidate_count == 0
        assert provider.track_calls == 2

        now[0] += timedelta(hours=2)
        provider.fail = False
        recovered = await revalidation.run(execution)
        assert recovered.candidate_count == 1
        assert recovered.processed_count == 1
        assert recovered.changed_count == 1
        assert recovered.failure_count == 0
        assert recovered.details[0].subject_id == str(source_id)
        assert recovered.details[0].outcome is JobRunDetailOutcome.CHANGED
        assert provider.track_calls == 3
        assert progress[-1] == JobProgress(1, 1, JobProgressUnit.RECORDS)

        async with units() as work:
            source = await CatalogRepository(work.session).source(source_id)
            assert source is not None
            assert source.availability is SourceAvailability.AVAILABLE
            assert source.failure_count == 0
            assert source.retry_at is None
            assert source.last_failure_code is None
        await service.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded maintenance owned by the Library module."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from nahoermaar.catalog.service import CatalogError, CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.observability import error_code
from nahoermaar.operations.jobs import (
    JobId,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
)
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)
from nahoermaar.operations.scheduler import (
    IntegerJobControl,
    JobControls,
    JobDefinition,
    JobDescriptor,
    JobExecution,
    JobProgressUnit,
    JobResult,
)

from .domain import MAX_PLAYLIST_ENTRIES, Playlist, PlaylistTrackSelection
from .repository import PlaylistRepository

_LABELS = ("Playlist undo receipts",)
_LOGGER = logging.getLogger(__name__)


class LibraryMaintenance:
    """Remove expired transient Library state."""

    __slots__ = ("_units",)

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution("library", _LABELS, self.run)

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with self._units() as work:
            removed = await PlaylistRepository(work.session).prune_entry_undos(
                context.now,
                limit=context.batch_size,
            )
            await work.commit()
        return (cleanup_detail("library", _LABELS[0], removed),)


@dataclass(frozen=True, slots=True)
class _PlaylistSyncResult:
    changed: int
    failed: int
    detail: JobRunDetail


class PlaylistSyncMaintenance:
    """Refresh linked playlists without discarding their last good contents."""

    __slots__ = (
        "_catalog",
        "_clock",
        "_interval",
        "_parallel_requests",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        catalog: CatalogService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(hours=1),
        parallel_requests: int = 4,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("Playlist sync interval must be positive.")
        if not 1 <= parallel_requests <= 10:
            raise ValueError(
                "Playlist sync parallel requests must be between 1 and 10."
            )
        self._units = units
        self._catalog = catalog
        self._clock = clock
        self._interval = interval
        self._parallel_requests = parallel_requests

    def definition(self) -> JobDefinition:
        return JobDefinition(
            JobDescriptor(
                id=JobId.PLAYLIST_SYNC,
                module="library",
                label="Playlist synchronization",
                description=(
                    "Refreshes linked playlists while preserving their last "
                    "successful contents."
                ),
                scope=("linked playlists", "provider order", "sync state"),
                interval=self._interval,
                controls=JobControls(IntegerJobControl(10, 1, 100)),
                parallel_requests=self._parallel_requests,
            ),
            self.run,
            run_on_startup=False,
        )

    async def run(self, execution: JobExecution) -> JobResult:
        now = self._clock().astimezone(UTC)
        candidates = await self._candidates(
            now - self._interval,
            execution.options.batch_size,
        )
        total = len(candidates)
        processed = 0
        execution.report_progress(0, total, JobProgressUnit.RECORDS)
        semaphore = asyncio.Semaphore(self._parallel_requests)

        async def synchronize(candidate: Playlist) -> _PlaylistSyncResult:
            nonlocal processed
            try:
                return await self._synchronize(candidate, now, semaphore)
            finally:
                processed += 1
                execution.report_progress(
                    processed,
                    total,
                    JobProgressUnit.RECORDS,
                )

        results = await asyncio.gather(*(synchronize(item) for item in candidates))
        changed = sum(result.changed for result in results)
        failures = sum(result.failed for result in results)
        _LOGGER.info(
            "library.playlist_sync_completed trigger=%s batch=%d candidates=%d "
            "changed=%d failures=%d",
            execution.trigger,
            execution.options.batch_size,
            total,
            changed,
            failures,
        )
        return JobResult(
            candidate_count=total,
            processed_count=processed,
            changed_count=changed,
            failure_count=failures,
            error_code="playlist_sync_partial" if failures else None,
            details=tuple(result.detail for result in results),
        )

    async def _candidates(
        self,
        due_before: datetime,
        limit: int,
    ) -> tuple[Playlist, ...]:
        async with self._units() as work:
            return await PlaylistRepository(work.session).linked_sync_candidates(
                due_before=due_before,
                limit=limit,
            )

    async def _synchronize(
        self,
        candidate: Playlist,
        now: datetime,
        semaphore: asyncio.Semaphore,
    ) -> _PlaylistSyncResult:
        source = candidate.source
        if source is None:
            return _PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        try:
            async with semaphore:
                materialized = await self._catalog.materialize_playlist(
                    source.canonical_url,
                    provider_key=source.provider_key,
                    max_entries=MAX_PLAYLIST_ENTRIES,
                )
        except CatalogError as error:
            return await self._failed_result(
                candidate,
                error_code(error),
                now,
            )

        if (
            materialized.provider_key != source.provider_key
            or materialized.external_id != source.external_id
        ):
            return await self._failed_result(
                candidate,
                "playlist_source_identity_mismatch",
                now,
            )

        selections = tuple(
            PlaylistTrackSelection(entry.track.id, entry.source.id)
            for entry in materialized.entries
        )
        async with self._units() as work:
            changes = await PlaylistRepository(work.session).synchronize_linked(
                candidate.id,
                provider_key=source.provider_key,
                external_id=source.external_id,
                canonical_url=materialized.canonical_url,
                selections=selections,
                unavailable_entry_count=materialized.unavailable_entry_count,
                truncated=materialized.truncated,
                now=now,
            )
            await work.commit()
        if changes is None:
            return _PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        affected = changes.added + changes.removed + changes.moved
        detail = JobRunDetail(
            kind=JobRunDetailKind.PLAYLIST_SYNC,
            outcome=(
                JobRunDetailOutcome.CHANGED
                if changes.content_changed
                else JobRunDetailOutcome.UNCHANGED
            ),
            label=candidate.name,
            summary=(
                f"{changes.added} added, {changes.removed} removed, "
                f"{changes.moved} moved, "
                f"{materialized.unavailable_entry_count} unavailable, "
                f"{changes.unchanged} unchanged."
            ),
            affected_count=affected,
            subject_id=str(candidate.id),
            source=source.provider_key,
        )
        return _PlaylistSyncResult(int(changes.content_changed), 0, detail)

    async def _failed_result(
        self,
        candidate: Playlist,
        code: str,
        now: datetime,
    ) -> _PlaylistSyncResult:
        source = candidate.source
        if source is None:
            return _PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        async with self._units() as work:
            recorded = await PlaylistRepository(work.session).mark_linked_sync_failed(
                candidate.id,
                provider_key=source.provider_key,
                external_id=source.external_id,
                error_code=code,
                now=now,
            )
            await work.commit()
        if not recorded:
            return _PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        _LOGGER.warning(
            "library.playlist_sync_failed playlist_id=%s provider=%s error=%s",
            candidate.id,
            source.provider_key,
            code,
        )
        return _PlaylistSyncResult(
            0,
            1,
            JobRunDetail(
                kind=JobRunDetailKind.PLAYLIST_SYNC,
                outcome=JobRunDetailOutcome.FAILED,
                label=candidate.name,
                summary="Synchronization failed; cached tracks were kept.",
                subject_id=str(candidate.id),
                source=source.provider_key,
                error_code=code,
            ),
        )

    @staticmethod
    def _skipped_detail(candidate: Playlist) -> JobRunDetail:
        return JobRunDetail(
            kind=JobRunDetailKind.PLAYLIST_SYNC,
            outcome=JobRunDetailOutcome.SKIPPED,
            label=candidate.name,
            summary="The playlist source changed before synchronization completed.",
            subject_id=str(candidate.id),
        )

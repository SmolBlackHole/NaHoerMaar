# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded maintenance owned by the Library module."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.operations.jobs import (
    JobId,
    JobRunDetail,
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

from .domain import Playlist
from .repository import PlaylistRepository
from .synchronization import PlaylistSyncResult, PlaylistSynchronizer

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


class PlaylistSyncMaintenance:
    """Refresh linked playlists without discarding their last good contents."""

    __slots__ = (
        "_clock",
        "_interval",
        "_parallel_requests",
        "_synchronizer",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        synchronizer: PlaylistSynchronizer,
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
        self._synchronizer = synchronizer
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

        async def synchronize(candidate: Playlist) -> PlaylistSyncResult:
            nonlocal processed
            try:
                async with semaphore:
                    return await self._synchronizer.synchronize(candidate, now=now)
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

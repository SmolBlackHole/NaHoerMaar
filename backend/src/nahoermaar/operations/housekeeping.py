# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Daily bounded cleanup for application-owned transient data."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from nahoermaar.catalog.repository import DiscoveryRepository
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.observability import error_code
from nahoermaar.operations.incidents import IncidentRepository, RETENTION_DAYS
from nahoermaar.player.repository import SessionRepository
from nahoermaar.users.domain import UserId
from nahoermaar.users.repository import AuthRepository

from .jobs import (
    JobId,
    JobRun,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
    JobService,
    JobTrigger,
)

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HousekeepingStatus:
    running: bool
    interval_seconds: float
    default_batch_size: int
    active_batch_size: int | None
    active_trigger: str | None
    active_candidates: int
    active_processed: int
    last_trigger: str | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    last_candidates: int
    last_processed: int
    last_changed: int
    last_failures: int
    last_error: str | None


class HousekeepingService:
    """Schedule and expose one bounded cleanup job."""

    __slots__ = (
        "_active_batch",
        "_active_processed",
        "_active_trigger",
        "_avatars",
        "_batch",
        "_clock",
        "_closed",
        "_interval",
        "_jobs",
        "_last_candidates",
        "_last_changed",
        "_last_error",
        "_last_failures",
        "_last_finished_at",
        "_last_processed",
        "_last_started_at",
        "_last_trigger",
        "_lock",
        "_manual_task",
        "_next_run_at",
        "_task",
        "_units",
    )

    def __init__(
        self,
        units: UnitFactory,
        jobs: JobService,
        avatars: DiscordAvatarStore,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(hours=1),
        batch_size: int = 10_000,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("Housekeeping interval must be positive.")
        self._validate_batch(batch_size)
        self._units = units
        self._jobs = jobs
        self._avatars = avatars
        self._clock = clock
        self._interval = interval
        self._batch = batch_size
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._manual_task: asyncio.Task[int] | None = None
        self._closed = False
        self._active_batch: int | None = None
        self._active_trigger: str | None = None
        self._active_processed = 0
        self._last_trigger: str | None = None
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._last_candidates = 0
        self._last_processed = 0
        self._last_changed = 0
        self._last_failures = 0
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Housekeeping is closed.")
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(),
            name="application-housekeeping",
        )

    def status(self) -> HousekeepingStatus:
        manual_running = self._manual_task is not None and not self._manual_task.done()
        return HousekeepingStatus(
            running=self._lock.locked() or manual_running,
            interval_seconds=self._interval.total_seconds(),
            default_batch_size=self._batch,
            active_batch_size=self._active_batch,
            active_trigger=self._active_trigger,
            active_candidates=6,
            active_processed=self._active_processed,
            last_trigger=self._last_trigger,
            last_started_at=self._last_started_at,
            last_finished_at=self._last_finished_at,
            next_run_at=self._next_run_at,
            last_candidates=self._last_candidates,
            last_processed=self._last_processed,
            last_changed=self._last_changed,
            last_failures=self._last_failures,
            last_error=self._last_error,
        )

    def trigger(
        self, batch_size: int | None = None, actor_id: UserId | None = None
    ) -> HousekeepingStatus:
        requested = self._batch if batch_size is None else batch_size
        self._validate_batch(requested)
        if self._lock.locked() or (
            self._manual_task is not None and not self._manual_task.done()
        ):
            return self.status()
        self._active_batch = requested
        self._active_trigger = JobTrigger.MANUAL
        task = asyncio.create_task(
            self._run(
                requested,
                trigger=JobTrigger.MANUAL,
                actor_id=actor_id,
            ),
            name="housekeeping-manual",
        )
        self._manual_task = task
        task.add_done_callback(self._manual_finished)
        return self.status()

    async def run(
        self,
        batch_size: int | None = None,
        *,
        trigger: JobTrigger = JobTrigger.MANUAL,
        actor_id: UserId | None = None,
    ) -> int:
        requested = self._batch if batch_size is None else batch_size
        self._validate_batch(requested)
        return await self._run(requested, trigger=trigger, actor_id=actor_id)

    async def _run(
        self,
        batch_size: int,
        *,
        trigger: JobTrigger,
        actor_id: UserId | None,
    ) -> int:
        async with self._lock:
            now = self._clock().astimezone(UTC)
            self._active_batch = batch_size
            self._active_trigger = trigger
            self._active_processed = 0
            self._last_trigger = trigger
            self._last_started_at = now
            self._last_error = None
            run: JobRun = await self._jobs.start_run(
                JobId.HOUSEKEEPING,
                trigger,
                batch_size,
                actor_id,
            )
            counts: list[int] = []
            try:
                async with self._units() as work:
                    login_attempts, sessions = await AuthRepository(work.session).prune(
                        now, limit=batch_size
                    )
                    counts.extend((login_attempts, sessions))
                    self._active_processed = 1
                    undos, receipts = await SessionRepository(work.session).prune(
                        now,
                        limit=batch_size,
                    )
                    counts.extend((undos, receipts))
                    self._active_processed = 2
                    snapshots = await DiscoveryRepository(work.session).prune_expired(
                        now, limit=batch_size
                    )
                    counts.append(snapshots)
                    self._active_processed = 3
                    incidents = await IncidentRepository(work.session).purge_before(
                        now - timedelta(days=RETENTION_DAYS),
                        limit=batch_size,
                    )
                    counts.append(incidents)
                    self._active_processed = 4
                    await work.commit()
                histories = await self._jobs.purge(now, batch_size)
                counts.append(histories)
                self._active_processed = 5
                avatars = await self._avatars.prune(now - timedelta(days=7))
                counts.append(avatars)
                self._active_processed = 6
            except asyncio.CancelledError:
                await self._jobs.cancel_run(run)
                raise
            except Exception as error:
                code = error_code(error)
                self._last_error = code
                self._last_failures = 1
                await self._jobs.fail_run(run, code)
                _LOGGER.exception(
                    "jobs.housekeeping_failed run_id=%s batch=%d",
                    run.id,
                    batch_size,
                )
                raise
            else:
                changed = sum(counts)
                self._last_candidates = changed
                self._last_processed = changed
                self._last_changed = changed
                self._last_failures = 0
                labels = (
                    "Login attempts",
                    "Browser sessions",
                    "Queue undo records",
                    "Operation receipts",
                    "Discovery snapshots",
                    "Incidents",
                    "Job runs",
                    "Discord avatars",
                )
                details = tuple(
                    JobRunDetail(
                        kind=JobRunDetailKind.DATA_CLEANUP,
                        outcome=(
                            JobRunDetailOutcome.CHANGED
                            if count
                            else JobRunDetailOutcome.UNCHANGED
                        ),
                        label=label,
                        summary=(
                            f"Removed {count} expired record"
                            f"{'s' if count != 1 else ''}."
                            if count
                            else "No expired records found."
                        ),
                        affected_count=count,
                    )
                    for label, count in zip(labels, counts, strict=True)
                )
                await self._jobs.finish_run(
                    run,
                    candidate_count=changed,
                    processed_count=changed,
                    changed_count=changed,
                    details=details,
                )
                _LOGGER.info(
                    "jobs.housekeeping_completed run_id=%s batch=%d "
                    "login_attempts=%d browser_sessions=%d queue_undos=%d "
                    "operation_receipts=%d discovery_snapshots=%d incidents=%d "
                    "job_runs=%d avatars=%d",
                    run.id,
                    batch_size,
                    *counts,
                )
                return changed
            finally:
                self._last_finished_at = self._clock().astimezone(UTC)
                self._active_batch = None
                self._active_trigger = None
                self._active_processed = 0

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        scheduled, self._task = self._task, None
        manual, self._manual_task = self._manual_task, None
        for task in (scheduled, manual):
            if task is not None:
                task.cancel()
        if scheduled is not None:
            await asyncio.gather(scheduled, return_exceptions=True)
        if manual is not None:
            await asyncio.gather(manual, return_exceptions=True)

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self._run(
                        self._batch,
                        trigger=JobTrigger.SCHEDULED,
                        actor_id=None,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    _LOGGER.warning(
                        "jobs.housekeeping_schedule_failed error=%s",
                        error_code(error),
                    )
                self._next_run_at = self._clock().astimezone(UTC) + self._interval
                await asyncio.sleep(self._interval.total_seconds())
        except asyncio.CancelledError:
            _LOGGER.info("jobs.housekeeping_stopped")

    def _manual_finished(self, task: asyncio.Task[int]) -> None:
        if self._manual_task is task:
            self._manual_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.error(
                "jobs.manual_housekeeping_failed error=%s",
                error_code(error),
            )

    @staticmethod
    def _validate_batch(batch_size: int) -> None:
        if not 1 <= batch_size <= 10_000:
            raise ValueError("Housekeeping batch must be between 1 and 10000.")

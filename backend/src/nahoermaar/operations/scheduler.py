# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Process-owned runtime for bounded application jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from typing import Protocol

from nahoermaar.observability import error_code
from nahoermaar.users.domain import UserId

from .jobs import (
    JobHealth,
    JobId,
    JobRun,
    JobRunDetail,
    JobTrigger,
)

type Clock = Callable[[], datetime]
type Sleeper = Callable[[float], Awaitable[None]]
type ProgressReporter = Callable[[int, int, "JobProgressUnit"], None]

_LOGGER = logging.getLogger(__name__)


class JobCoordinatorErrorCode(StrEnum):
    UNKNOWN_JOB = "unknown_job"
    ALREADY_RUNNING = "job_already_running"
    INVALID_OPTIONS = "invalid_job_options"
    NOT_RUNNING = "job_coordinator_not_running"
    CLOSED = "job_coordinator_closed"


class JobCoordinatorError(RuntimeError):
    """Stable failure raised by the shared job runtime."""

    def __init__(self, code: JobCoordinatorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class JobProgressUnit(StrEnum):
    RECORDS = "records"
    STEPS = "steps"


@dataclass(frozen=True, slots=True)
class IntegerJobControl:
    default: int
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if self.minimum < 1:
            raise ValueError("Job control minimum must be positive.")
        if self.maximum < self.minimum:
            raise ValueError("Job control maximum must not be below its minimum.")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("Job control default must be inside its bounds.")

    def resolve(self, value: int | None, name: str) -> int:
        resolved = self.default if value is None else value
        if not self.minimum <= resolved <= self.maximum:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.INVALID_OPTIONS,
                f"{name} must be between {self.minimum} and {self.maximum}.",
            )
        return resolved


@dataclass(frozen=True, slots=True)
class BooleanJobControl:
    default: bool


@dataclass(frozen=True, slots=True)
class JobControls:
    batch_size: IntegerJobControl
    preview: BooleanJobControl | None = None
    age_days: IntegerJobControl | None = None

    def resolve(self, request: JobRunRequest) -> JobOptions:
        if self.preview is None and request.preview is not None:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.INVALID_OPTIONS,
                "This job does not support preview mode.",
            )
        if self.age_days is None and request.age_days is not None:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.INVALID_OPTIONS,
                "This job does not support an age boundary.",
            )
        return JobOptions(
            batch_size=self.batch_size.resolve(request.batch_size, "batch_size"),
            preview=(
                None
                if self.preview is None
                else self.preview.default
                if request.preview is None
                else request.preview
            ),
            age_days=(
                None
                if self.age_days is None
                else self.age_days.resolve(request.age_days, "age_days")
            ),
        )


@dataclass(frozen=True, slots=True)
class JobRunRequest:
    batch_size: int | None = None
    preview: bool | None = None
    age_days: int | None = None


@dataclass(frozen=True, slots=True)
class JobOptions:
    batch_size: int
    preview: bool | None = None
    age_days: int | None = None


@dataclass(frozen=True, slots=True)
class JobProgress:
    current: int
    total: int
    unit: JobProgressUnit

    def __post_init__(self) -> None:
        if self.current < 0 or self.total < 0:
            raise ValueError("Job progress values must not be negative.")
        if self.current > self.total:
            raise ValueError("Job progress current must not exceed total.")


@dataclass(frozen=True, slots=True)
class JobResult:
    candidate_count: int
    processed_count: int
    changed_count: int
    failure_count: int = 0
    error_code: str | None = None
    details: tuple[JobRunDetail, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.candidate_count,
            self.processed_count,
            self.changed_count,
            self.failure_count,
        )
        if any(count < 0 for count in counts):
            raise ValueError("Job result counts must not be negative.")


@dataclass(frozen=True, slots=True)
class JobExecution:
    options: JobOptions
    trigger: JobTrigger
    actor_id: UserId | None
    _reporter: ProgressReporter = field(repr=False)

    def report_progress(
        self,
        current: int,
        total: int,
        unit: JobProgressUnit,
    ) -> None:
        self._reporter(current, total, unit)


type JobRunner = Callable[[JobExecution], Awaitable[JobResult]]


@dataclass(frozen=True, slots=True)
class JobDescriptor:
    id: JobId
    module: str
    label: str
    description: str
    scope: tuple[str, ...]
    interval: timedelta
    controls: JobControls
    parallel_requests: int = 1

    def __post_init__(self) -> None:
        if not self.module.strip() or not self.label.strip():
            raise ValueError("Job module and label must not be empty.")
        if self.interval <= timedelta(0):
            raise ValueError("Job interval must be positive.")
        if self.parallel_requests < 1:
            raise ValueError("Job parallelism must be positive.")


@dataclass(frozen=True, slots=True)
class JobDefinition:
    descriptor: JobDescriptor
    runner: JobRunner
    run_on_startup: bool = True


@dataclass(frozen=True, slots=True)
class JobStatus:
    descriptor: JobDescriptor
    health: JobHealth
    running: bool
    active_options: JobOptions | None
    active_trigger: JobTrigger | None
    progress: JobProgress | None
    last_trigger: JobTrigger | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    last_candidates: int
    last_processed: int
    last_changed: int
    last_failures: int
    last_error: str | None


class JobRunRecorder(Protocol):
    async def start_run(
        self,
        job_id: JobId | str,
        trigger: JobTrigger | str,
        requested_count: int,
        requested_by: UserId | None = None,
    ) -> JobRun: ...

    async def finish_run(
        self,
        run: JobRun,
        *,
        candidate_count: int,
        processed_count: int,
        changed_count: int,
        failure_count: int = 0,
        error_code: str | None = None,
        details: tuple[JobRunDetail, ...] = (),
    ) -> JobRun: ...

    async def fail_run(self, run: JobRun, error_code: str) -> JobRun: ...

    async def cancel_run(self, run: JobRun) -> JobRun: ...

    async def health(self, job_ids: tuple[JobId, ...]) -> dict[JobId, JobHealth]: ...


@dataclass(slots=True)
class _Runtime:
    definition: JobDefinition
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    claimed: bool = False
    schedule_task: asyncio.Task[None] | None = None
    manual_task: asyncio.Task[JobResult] | None = None
    active_options: JobOptions | None = None
    active_trigger: JobTrigger | None = None
    progress: JobProgress | None = None
    last_trigger: JobTrigger | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    next_run_at: datetime | None = None
    last_candidates: int = 0
    last_processed: int = 0
    last_changed: int = 0
    last_failures: int = 0
    last_error: str | None = None

    @property
    def running(self) -> bool:
        return self.claimed


class JobCoordinator:
    """Schedule registered jobs and expose one shared runtime contract."""

    __slots__ = (
        "_clock",
        "_closed",
        "_recorder",
        "_runtimes",
        "_sleeper",
        "_started",
    )

    def __init__(
        self,
        recorder: JobRunRecorder,
        definitions: Iterable[JobDefinition],
        *,
        clock: Clock = lambda: datetime.now(UTC),
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        runtimes: dict[JobId, _Runtime] = {}
        for definition in definitions:
            job_id = definition.descriptor.id
            if job_id in runtimes:
                raise ValueError(f"Job {job_id.value} is registered more than once.")
            runtimes[job_id] = _Runtime(definition)
        self._recorder = recorder
        self._runtimes = runtimes
        self._clock = clock
        self._sleeper = sleeper
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.CLOSED,
                "The job coordinator is closed.",
            )
        if self._started:
            return
        self._started = True
        for runtime in self._runtimes.values():
            now = self._clock().astimezone(UTC)
            runtime.next_run_at = (
                now
                if runtime.definition.run_on_startup
                else now + runtime.definition.descriptor.interval
            )
            runtime.schedule_task = asyncio.create_task(
                self._schedule(runtime),
                name=f"job-schedule-{runtime.definition.descriptor.id.value}",
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        scheduled_tasks: list[asyncio.Task[None]] = []
        manual_tasks: list[asyncio.Task[JobResult]] = []
        for runtime in self._runtimes.values():
            scheduled = runtime.schedule_task
            manual = runtime.manual_task
            runtime.schedule_task = None
            runtime.manual_task = None
            if scheduled is not None:
                scheduled.cancel()
                scheduled_tasks.append(scheduled)
            if manual is not None:
                manual.cancel()
                manual_tasks.append(manual)
        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks, return_exceptions=True)
        if manual_tasks:
            await asyncio.gather(*manual_tasks, return_exceptions=True)

    async def trigger(
        self,
        job_id: JobId | str,
        request: JobRunRequest,
        actor_id: UserId | None,
    ) -> JobStatus:
        self._ensure_running()
        runtime = self._runtime(job_id)
        options = runtime.definition.descriptor.controls.resolve(request)
        if runtime.running:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.ALREADY_RUNNING,
                f"Job {runtime.definition.descriptor.id.value} is already running.",
            )
        runtime.claimed = True
        runtime.active_options = options
        runtime.active_trigger = JobTrigger.MANUAL
        task = asyncio.create_task(
            self._execute(runtime, options, JobTrigger.MANUAL, actor_id),
            name=f"job-manual-{runtime.definition.descriptor.id.value}",
        )
        runtime.manual_task = task

        def finished(completed: asyncio.Task[JobResult]) -> None:
            self._manual_finished(runtime, completed)

        task.add_done_callback(finished)
        return await self.status(runtime.definition.descriptor.id)

    async def statuses(self) -> tuple[JobStatus, ...]:
        job_ids = tuple(self._runtimes)
        health = await self._recorder.health(job_ids)
        return tuple(
            self._status(runtime, health[job_id])
            for job_id, runtime in self._runtimes.items()
        )

    async def status(self, job_id: JobId | str) -> JobStatus:
        runtime = self._runtime(job_id)
        health = await self._recorder.health((runtime.definition.descriptor.id,))
        return self._status(
            runtime,
            health[runtime.definition.descriptor.id],
        )

    def descriptor(self, job_id: JobId | str) -> JobDescriptor:
        return self._runtime(job_id).definition.descriptor

    async def _schedule(self, runtime: _Runtime) -> None:
        descriptor = runtime.definition.descriptor
        try:
            if not runtime.definition.run_on_startup:
                runtime.next_run_at = (
                    self._clock().astimezone(UTC) + descriptor.interval
                )
                await self._sleeper(descriptor.interval.total_seconds())
            while True:
                if not runtime.running:
                    options = descriptor.controls.resolve(JobRunRequest())
                    runtime.claimed = True
                    try:
                        await self._execute(
                            runtime,
                            options,
                            JobTrigger.SCHEDULED,
                            None,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        _LOGGER.warning(
                            "jobs.schedule_failed job=%s error=%s",
                            descriptor.id.value,
                            error_code(error),
                        )
                runtime.next_run_at = (
                    self._clock().astimezone(UTC) + descriptor.interval
                )
                await self._sleeper(descriptor.interval.total_seconds())
        except asyncio.CancelledError:
            _LOGGER.info("jobs.schedule_stopped job=%s", descriptor.id.value)
            raise

    async def _execute(
        self,
        runtime: _Runtime,
        options: JobOptions,
        trigger: JobTrigger,
        actor_id: UserId | None,
    ) -> JobResult:
        descriptor = runtime.definition.descriptor
        async with runtime.lock:
            runtime.active_options = options
            runtime.active_trigger = trigger
            runtime.progress = None
            runtime.last_trigger = trigger
            runtime.last_started_at = self._clock().astimezone(UTC)
            runtime.last_error = None
            run: JobRun | None = None
            try:
                run = await self._recorder.start_run(
                    descriptor.id,
                    trigger,
                    options.batch_size,
                    actor_id,
                )
                execution = JobExecution(
                    options,
                    trigger,
                    actor_id,
                    lambda current, total, unit: self._report_progress(
                        runtime, current, total, unit
                    ),
                )
                result = await runtime.definition.runner(execution)
            except asyncio.CancelledError:
                if run is not None:
                    await self._recorder.cancel_run(run)
                raise
            except Exception as error:
                code = error_code(error)
                runtime.last_error = code
                runtime.last_failures = 1
                if run is not None:
                    await self._recorder.fail_run(run, code)
                raise
            else:
                await self._recorder.finish_run(
                    run,
                    candidate_count=result.candidate_count,
                    processed_count=result.processed_count,
                    changed_count=result.changed_count,
                    failure_count=result.failure_count,
                    error_code=result.error_code,
                    details=result.details,
                )
                runtime.last_candidates = result.candidate_count
                runtime.last_processed = result.processed_count
                runtime.last_changed = result.changed_count
                runtime.last_failures = result.failure_count
                runtime.last_error = result.error_code
                return result
            finally:
                runtime.last_finished_at = self._clock().astimezone(UTC)
                runtime.claimed = False
                runtime.active_options = None
                runtime.active_trigger = None
                runtime.progress = None

    @staticmethod
    def _report_progress(
        runtime: _Runtime,
        current: int,
        total: int,
        unit: JobProgressUnit,
    ) -> None:
        runtime.progress = JobProgress(current, total, unit)

    @staticmethod
    def _status(runtime: _Runtime, health: JobHealth) -> JobStatus:
        return JobStatus(
            descriptor=runtime.definition.descriptor,
            health=health,
            running=runtime.running,
            active_options=runtime.active_options,
            active_trigger=runtime.active_trigger,
            progress=runtime.progress,
            last_trigger=runtime.last_trigger,
            last_started_at=runtime.last_started_at,
            last_finished_at=runtime.last_finished_at,
            next_run_at=runtime.next_run_at,
            last_candidates=runtime.last_candidates,
            last_processed=runtime.last_processed,
            last_changed=runtime.last_changed,
            last_failures=runtime.last_failures,
            last_error=runtime.last_error,
        )

    def _runtime(self, job_id: JobId | str) -> _Runtime:
        try:
            resolved = JobId(job_id)
            return self._runtimes[resolved]
        except (KeyError, ValueError) as error:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.UNKNOWN_JOB,
                f"Unknown job {job_id}.",
            ) from error

    def _ensure_running(self) -> None:
        if self._closed:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.CLOSED,
                "The job coordinator is closed.",
            )
        if not self._started:
            raise JobCoordinatorError(
                JobCoordinatorErrorCode.NOT_RUNNING,
                "The job coordinator is not running.",
            )

    @staticmethod
    def _manual_finished(
        runtime: _Runtime,
        task: asyncio.Task[JobResult],
    ) -> None:
        if runtime.manual_task is task:
            runtime.manual_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.error(
                "jobs.manual_failed job=%s error=%s",
                runtime.definition.descriptor.id.value,
                error_code(error),
            )

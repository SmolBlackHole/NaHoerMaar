# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nahoermaar.operations.jobs import (
    JobHealth,
    JobId,
    JobRun,
    JobRunDetail,
    JobRunStatus,
    JobTrigger,
)
from nahoermaar.operations.scheduler import (
    BooleanJobControl,
    IntegerJobControl,
    JobControls,
    JobCoordinator,
    JobCoordinatorError,
    JobCoordinatorErrorCode,
    JobDefinition,
    JobDescriptor,
    JobExecution,
    JobProgressUnit,
    JobResult,
    JobRunner,
    JobRunRequest,
)
from nahoermaar.users.domain import UserId

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


class _Recorder:
    def __init__(self) -> None:
        self.runs: list[JobRun] = []
        self.finished: list[JobRun] = []
        self.failed: list[JobRun] = []
        self.cancelled: list[JobRun] = []
        self.health_by_job: dict[JobId, JobHealth] = {}

    async def start_run(
        self,
        job_id: JobId | str,
        trigger: JobTrigger | str,
        requested_count: int,
        requested_by: UserId | None = None,
    ) -> JobRun:
        run = JobRun(
            uuid4(),
            JobId(job_id),
            JobTrigger(trigger),
            JobRunStatus.RUNNING,
            requested_by,
            requested_count,
            0,
            0,
            0,
            0,
            NOW,
        )
        self.runs.append(run)
        return run

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
    ) -> JobRun:
        status = JobRunStatus.PARTIAL if failure_count else JobRunStatus.SUCCEEDED
        finished = replace(
            run,
            status=status,
            candidate_count=candidate_count,
            processed_count=processed_count,
            changed_count=changed_count,
            failure_count=failure_count,
            finished_at=NOW,
            error_code=error_code,
            details=details,
        )
        self.finished.append(finished)
        self.health_by_job[run.job_id] = (
            JobHealth.NEEDS_ATTENTION if failure_count else JobHealth.HEALTHY
        )
        return finished

    async def fail_run(self, run: JobRun, error_code: str) -> JobRun:
        failed = replace(
            run,
            status=JobRunStatus.FAILED,
            failure_count=1,
            finished_at=NOW,
            error_code=error_code,
        )
        self.failed.append(failed)
        self.health_by_job[run.job_id] = JobHealth.NEEDS_ATTENTION
        return failed

    async def cancel_run(self, run: JobRun) -> JobRun:
        cancelled = replace(
            run,
            status=JobRunStatus.CANCELLED,
            finished_at=NOW,
            error_code="job_cancelled",
        )
        self.cancelled.append(cancelled)
        return cancelled

    async def health(self, job_ids: tuple[JobId, ...]) -> dict[JobId, JobHealth]:
        return {
            job_id: self.health_by_job.get(job_id, JobHealth.UNKNOWN)
            for job_id in job_ids
        }


def _definition(
    job_id: JobId,
    runner: JobRunner,
    *,
    run_on_startup: bool = False,
    preview: bool = False,
) -> JobDefinition:
    return JobDefinition(
        JobDescriptor(
            job_id,
            "catalog" if job_id is JobId.CATALOG_MAINTENANCE else "operations",
            job_id.value,
            "Test job",
            ("Test one bounded operation",),
            timedelta(hours=1),
            JobControls(
                IntegerJobControl(10, 1, 100),
                BooleanJobControl(True) if preview else None,
            ),
        ),
        runner,
        run_on_startup=run_on_startup,
    )


def test_job_coordinator_validates_registration_and_controls() -> None:
    recorder = _Recorder()

    async def complete(_execution: JobExecution) -> JobResult:
        return JobResult(0, 0, 0)

    definition = _definition(JobId.CATALOG_MAINTENANCE, complete)
    with pytest.raises(ValueError, match="registered more than once"):
        JobCoordinator(recorder, (definition, definition))

    coordinator = JobCoordinator(recorder, (definition,))

    async def scenario() -> None:
        with pytest.raises(JobCoordinatorError) as not_running:
            await coordinator.trigger(
                JobId.CATALOG_MAINTENANCE,
                JobRunRequest(),
                None,
            )
        assert not_running.value.code is JobCoordinatorErrorCode.NOT_RUNNING

        await coordinator.start()
        with pytest.raises(JobCoordinatorError) as invalid_batch:
            await coordinator.trigger(
                JobId.CATALOG_MAINTENANCE,
                JobRunRequest(batch_size=101),
                None,
            )
        assert invalid_batch.value.code is JobCoordinatorErrorCode.INVALID_OPTIONS

        with pytest.raises(JobCoordinatorError) as invalid_preview:
            await coordinator.trigger(
                JobId.CATALOG_MAINTENANCE,
                JobRunRequest(preview=True),
                None,
            )
        assert invalid_preview.value.code is JobCoordinatorErrorCode.INVALID_OPTIONS

        with pytest.raises(JobCoordinatorError) as unknown:
            await coordinator.trigger("missing", JobRunRequest(), None)
        assert unknown.value.code is JobCoordinatorErrorCode.UNKNOWN_JOB
        await coordinator.close()

    asyncio.run(scenario())


def test_manual_job_reports_progress_and_rejects_overlap() -> None:
    recorder = _Recorder()
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(execution: JobExecution) -> JobResult:
        execution.report_progress(1, 2, JobProgressUnit.STEPS)
        started.set()
        await release.wait()
        execution.report_progress(2, 2, JobProgressUnit.STEPS)
        return JobResult(8, 8, 3)

    coordinator = JobCoordinator(
        recorder,
        (_definition(JobId.CATALOG_MAINTENANCE, runner),),
        clock=lambda: NOW,
    )
    actor_id = UserId(uuid4())

    async def scenario() -> None:
        await coordinator.start()
        accepted = await coordinator.trigger(
            JobId.CATALOG_MAINTENANCE,
            JobRunRequest(batch_size=8),
            actor_id,
        )
        assert accepted.running is True
        assert accepted.active_options is not None
        assert accepted.active_options.batch_size == 8
        await started.wait()

        active = await coordinator.status(JobId.CATALOG_MAINTENANCE)
        assert active.progress is not None
        assert active.progress.current == 1
        assert active.progress.total == 2
        assert active.progress.unit is JobProgressUnit.STEPS

        with pytest.raises(JobCoordinatorError) as overlap:
            await coordinator.trigger(
                JobId.CATALOG_MAINTENANCE,
                JobRunRequest(),
                actor_id,
            )
        assert overlap.value.code is JobCoordinatorErrorCode.ALREADY_RUNNING

        release.set()
        while (await coordinator.status(JobId.CATALOG_MAINTENANCE)).running:
            await asyncio.sleep(0)

        completed = await coordinator.status(JobId.CATALOG_MAINTENANCE)
        assert completed.health is JobHealth.HEALTHY
        assert completed.last_candidates == 8
        assert completed.last_processed == 8
        assert completed.last_changed == 3
        assert completed.progress is None
        assert recorder.runs[0].requested_by == actor_id
        assert recorder.runs[0].requested_count == 8
        await coordinator.close()

    asyncio.run(scenario())


def test_coordinator_cancels_active_run_on_shutdown() -> None:
    recorder = _Recorder()
    started = asyncio.Event()

    async def runner(_execution: JobExecution) -> JobResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("Unreachable")

    coordinator = JobCoordinator(
        recorder,
        (_definition(JobId.HOUSEKEEPING, runner),),
        clock=lambda: NOW,
    )

    async def scenario() -> None:
        await coordinator.start()
        await coordinator.trigger(JobId.HOUSEKEEPING, JobRunRequest(), None)
        await started.wait()
        await coordinator.close()
        assert len(recorder.cancelled) == 1
        assert recorder.cancelled[0].status is JobRunStatus.CANCELLED

    asyncio.run(scenario())


def test_scheduled_failure_does_not_stop_later_runs() -> None:
    recorder = _Recorder()
    recovered = asyncio.Event()
    attempts = 0
    sleeps = 0
    hold = asyncio.Event()

    async def runner(_execution: JobExecution) -> JobResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("provider unavailable")
        recovered.set()
        return JobResult(1, 1, 1)

    async def sleeper(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            return
        await hold.wait()

    coordinator = JobCoordinator(
        recorder,
        (
            _definition(
                JobId.CATALOG_MAINTENANCE,
                runner,
                run_on_startup=True,
            ),
        ),
        clock=lambda: NOW,
        sleeper=sleeper,
    )

    async def scenario() -> None:
        await coordinator.start()
        await asyncio.wait_for(recovered.wait(), timeout=1)
        assert len(recorder.failed) == 1
        assert len(recorder.finished) == 1
        assert (
            await coordinator.status(JobId.CATALOG_MAINTENANCE)
        ).health is JobHealth.HEALTHY
        await coordinator.close()

    asyncio.run(scenario())

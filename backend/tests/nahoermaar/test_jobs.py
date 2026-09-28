# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

from nahoermaar.database.core import Database
from nahoermaar.database.schema import migrate
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.catalog.maintenance import CatalogHousekeeping
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.integrations.maintenance import IntegrationsMaintenance
from nahoermaar.operations.incidents import IncidentPeriod, IncidentService
from nahoermaar.operations.jobs import (
    JobHealth,
    JobId,
    JobRun,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
    JobRunStatus,
    JobRunService,
    JobTrigger,
)
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.operations.main import (
    complete_operations_module,
    create_operations_foundation,
)
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    HousekeepingMaintenance,
    cleanup_detail,
)
from nahoermaar.operations.scheduler import (
    JobExecution,
    JobOptions,
    JobProgressUnit,
    JobRunRequest,
)
from nahoermaar.player.maintenance import PlayerMaintenance
from nahoermaar.users.maintenance import UsersMaintenance

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def test_job_runs_persist_failures_recovery_and_interruption() -> None:
    database = Database(os.environ["DATABASE_URL"])
    current = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    incidents = IncidentService(units, clock=lambda: current[0])
    jobs = JobRunService(units, incidents, clock=lambda: current[0])

    async def scenario() -> None:
        await migrate(database.engine)
        await jobs.reconcile_interrupted_runs()
        assert await jobs.health((JobId.CATALOG_MAINTENANCE, JobId.HOUSEKEEPING)) == {
            JobId.CATALOG_MAINTENANCE: JobHealth.UNKNOWN,
            JobId.HOUSEKEEPING: JobHealth.UNKNOWN,
        }

        partial = await jobs.start_run(
            JobId.CATALOG_MAINTENANCE,
            JobTrigger.SCHEDULED,
            10,
        )
        current[0] += timedelta(seconds=4)
        partial = await jobs.finish_run(
            partial,
            candidate_count=10,
            processed_count=10,
            changed_count=8,
            failure_count=2,
            error_code="catalog_maintenance_partial",
            details=(
                JobRunDetail(
                    kind=JobRunDetailKind.TRACK_METADATA,
                    outcome=JobRunDetailOutcome.FAILED,
                    label="ending",
                    summary="The provider could not resolve this track.",
                    subject_id="965bbd24-0353-426f-880f-fd717f56d711",
                    source="youtube",
                    error_code="provider_failed",
                ),
            ),
        )
        assert partial.status is JobRunStatus.PARTIAL
        assert partial.duration_seconds == 4
        assert await jobs.health((JobId.CATALOG_MAINTENANCE,)) == {
            JobId.CATALOG_MAINTENANCE: JobHealth.NEEDS_ATTENTION
        }

        current[0] += timedelta(minutes=5)
        recovered = await jobs.start_run(
            JobId.CATALOG_MAINTENANCE,
            JobTrigger.MANUAL,
            5,
        )
        recovered = await jobs.finish_run(
            recovered,
            candidate_count=5,
            processed_count=5,
            changed_count=5,
        )
        assert recovered.status is JobRunStatus.SUCCEEDED
        assert await jobs.health((JobId.CATALOG_MAINTENANCE,)) == {
            JobId.CATALOG_MAINTENANCE: JobHealth.HEALTHY
        }

        current[0] += timedelta(seconds=1)
        interrupted = await jobs.start_run(
            JobId.HOUSEKEEPING,
            JobTrigger.SCHEDULED,
            1000,
        )
        current[0] += timedelta(seconds=1)
        await jobs.reconcile_interrupted_runs()

        cancelled = await jobs.start_run(
            JobId.HOUSEKEEPING,
            JobTrigger.MANUAL,
            10,
        )
        cancelled = await jobs.cancel_run(cancelled)
        assert cancelled.status is JobRunStatus.CANCELLED
        assert await jobs.health((JobId.HOUSEKEEPING,)) == {
            JobId.HOUSEKEEPING: JobHealth.NEEDS_ATTENTION
        }

        history = await jobs.recent()
        assert [run.status for run in history] == [
            JobRunStatus.CANCELLED,
            JobRunStatus.FAILED,
            JobRunStatus.SUCCEEDED,
            JobRunStatus.PARTIAL,
        ]
        assert history[1].id == interrupted.id
        assert history[1].error_code == "job_interrupted"
        assert history[3].details == partial.details

        first_page = await jobs.runs(limit=2)
        assert [run.id for run in first_page.entries] == [
            history[0].id,
            history[1].id,
        ]
        assert all(not run.details for run in first_page.entries)
        assert first_page.next_cursor is not None
        second_page = await jobs.runs(limit=2, cursor=first_page.next_cursor)
        assert [run.id for run in second_page.entries] == [
            history[2].id,
            history[3].id,
        ]
        assert second_page.next_cursor is None

        partial_runs = await jobs.runs(limit=20, status=JobRunStatus.PARTIAL)
        assert [run.id for run in partial_runs.entries] == [partial.id]
        catalog_runs = await jobs.runs(limit=20, job_id=JobId.CATALOG_MAINTENANCE)
        assert [run.id for run in catalog_runs.entries] == [
            recovered.id,
            partial.id,
        ]
        loaded_partial = await jobs.run(partial.id)
        assert loaded_partial is not None
        assert loaded_partial.details == partial.details

        report = await incidents.report(IncidentPeriod.HOURS_24)
        assert report.totals.warnings == 2
        assert report.totals.errors == 1
        assert report.totals.recoveries == 1
        assert {incident.error_code for incident in report.items} == {
            "catalog_maintenance_partial",
            "job_interrupted",
        }

        current[0] += timedelta(days=31)
        assert await jobs.purge(current[0], limit=10) == 4
        assert await jobs.recent() == ()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_job_run_cursor_is_stable_for_equal_timestamps() -> None:
    database = Database(os.environ["DATABASE_URL"])

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    incidents = IncidentService(units, clock=lambda: NOW)
    jobs = JobRunService(units, incidents, clock=lambda: NOW)

    async def scenario() -> None:
        await migrate(database.engine)
        runs: list[JobRun] = []
        for _ in range(3):
            run = await jobs.start_run(
                JobId.CATALOG_MAINTENANCE,
                JobTrigger.MANUAL,
                1,
            )
            runs.append(
                await jobs.finish_run(
                    run,
                    candidate_count=1,
                    processed_count=1,
                    changed_count=1,
                )
            )

        expected = sorted(runs, key=lambda run: run.id, reverse=True)
        first_page = await jobs.runs(limit=2)
        assert list(first_page.entries) == expected[:2]
        assert first_page.next_cursor is not None
        second_page = await jobs.runs(limit=2, cursor=first_page.next_cursor)
        assert list(second_page.entries) == expected[2:]
        assert second_page.next_cursor is None

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_housekeeping_records_a_bounded_manual_run(tmp_path: Path) -> None:
    database = Database(os.environ["DATABASE_URL"])

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    foundation = create_operations_foundation(units, RecentLogBuffer())
    jobs = foundation.job_runs
    operations = complete_operations_module(
        foundation,
        (),
        (
            UsersMaintenance(units).contribution(),
            PlayerMaintenance(units).contribution(),
            CatalogHousekeeping(units).contribution(),
            *foundation.housekeeping,
            IntegrationsMaintenance(
                DiscordAvatarStore(tmp_path / "avatars")
            ).contribution(),
        ),
    )
    coordinator = operations.jobs

    async def scenario() -> None:
        await migrate(database.engine)
        await coordinator.start()
        status = await coordinator.trigger(
            JobId.HOUSEKEEPING,
            JobRunRequest(batch_size=10),
            None,
        )
        assert status.running
        while (await coordinator.status(JobId.HOUSEKEEPING)).running:
            await asyncio.sleep(0)

        status = await coordinator.status(JobId.HOUSEKEEPING)
        assert status.running is False
        assert status.last_trigger == JobTrigger.MANUAL
        assert status.last_processed == 0
        assert status.last_failures == 0

        history = await jobs.recent()
        assert len(history) == 1
        assert history[0].job_id is JobId.HOUSEKEEPING
        assert history[0].status is JobRunStatus.SUCCEEDED
        assert history[0].requested_count == 10
        assert [detail.label for detail in history[0].details] == [
            "Login attempts",
            "Browser sessions",
            "Queue undo records",
            "Operation receipts",
            "Discovery snapshots",
            "Incidents",
            "Job runs",
            "Discord avatars",
        ]
        assert all(
            detail.outcome is JobRunDetailOutcome.UNCHANGED
            for detail in history[0].details
        )
        await coordinator.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_housekeeping_continues_after_one_module_fails() -> None:
    progress: list[tuple[int, int, JobProgressUnit]] = []

    async def broken(_context: HousekeepingContext) -> tuple[JobRunDetail, ...]:
        raise RuntimeError("database unavailable")

    async def healthy(_context: HousekeepingContext) -> tuple[JobRunDetail, ...]:
        return (cleanup_detail("catalog", "Discovery snapshots", 2),)

    maintenance = HousekeepingMaintenance(
        (
            HousekeepingContribution("users", ("Browser sessions",), broken),
            HousekeepingContribution(
                "catalog",
                ("Discovery snapshots",),
                healthy,
            ),
        ),
        clock=lambda: NOW,
    )

    async def scenario() -> None:
        result = await maintenance.run(
            JobExecution(
                JobOptions(batch_size=10),
                JobTrigger.MANUAL,
                None,
                lambda current, total, unit: progress.append((current, total, unit)),
            )
        )
        assert result.changed_count == 2
        assert result.failure_count == 1
        assert result.error_code == "housekeeping_partial"
        assert [detail.outcome for detail in result.details] == [
            JobRunDetailOutcome.FAILED,
            JobRunDetailOutcome.CHANGED,
        ]
        assert progress == [
            (0, 2, JobProgressUnit.STEPS),
            (1, 2, JobProgressUnit.STEPS),
            (2, 2, JobProgressUnit.STEPS),
        ]

    asyncio.run(scenario())

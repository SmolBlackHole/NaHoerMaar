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
from nahoermaar.operations.incidents import IncidentPeriod, IncidentService
from nahoermaar.operations.housekeeping import HousekeepingService
from nahoermaar.operations.jobs import (
    JobId,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
    JobRunStatus,
    JobService,
    JobTrigger,
)
from nahoermaar.integrations.avatars import DiscordAvatarStore

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def test_job_runs_persist_failures_recovery_and_interruption() -> None:
    database = Database(os.environ["DATABASE_URL"])
    current = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    incidents = IncidentService(units, clock=lambda: current[0])
    jobs = JobService(units, incidents, clock=lambda: current[0])

    async def scenario() -> None:
        await migrate(database.engine)
        await jobs.start()

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

        current[0] += timedelta(seconds=1)
        interrupted = await jobs.start_run(
            JobId.HOUSEKEEPING,
            JobTrigger.SCHEDULED,
            1000,
        )
        current[0] += timedelta(seconds=1)
        await jobs.start()

        cancelled = await jobs.start_run(
            JobId.HOUSEKEEPING,
            JobTrigger.MANUAL,
            10,
        )
        cancelled = await jobs.cancel_run(cancelled)
        assert cancelled.status is JobRunStatus.CANCELLED

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

        report = await incidents.report(IncidentPeriod.HOURS_24)
        assert report.totals.warnings == 2
        assert report.totals.errors == 1
        assert report.totals.recoveries == 1
        assert {incident.error_code for incident in report.recent} == {
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


def test_housekeeping_records_a_bounded_manual_run(tmp_path: Path) -> None:
    database = Database(os.environ["DATABASE_URL"])

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    incidents = IncidentService(units, clock=lambda: NOW)
    jobs = JobService(units, incidents, clock=lambda: NOW)
    housekeeping = HousekeepingService(
        units,
        jobs,
        DiscordAvatarStore(tmp_path / "avatars"),
        clock=lambda: NOW,
    )

    async def scenario() -> None:
        await migrate(database.engine)
        assert await housekeeping.run(batch_size=10) == 0

        status = housekeeping.status()
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

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

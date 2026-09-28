# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Operations foundation and completed runtime composition."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.lifecycle import LifecycleResource

from .incidents import IncidentRepository, IncidentService, RETENTION_DAYS
from .jobs import JobRunDetail, JobRunService
from .logs import RecentLogBuffer
from .maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    HousekeepingMaintenance,
    cleanup_detail,
)
from .scheduler import JobCoordinator, JobDefinition

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class OperationsFoundation:
    """Operations services and retention work required during composition."""

    incidents: IncidentService
    job_runs: JobRunService
    logs: RecentLogBuffer
    housekeeping: tuple[HousekeepingContribution, ...]


@dataclass(frozen=True, slots=True)
class OperationsModule:
    """Completed Operations runtime and its lifecycle resources."""

    incidents: IncidentService
    job_runs: JobRunService
    jobs: JobCoordinator
    logs: RecentLogBuffer
    reconciliation_lifecycle: LifecycleResource
    jobs_lifecycle: LifecycleResource


def create_operations_foundation(
    units: UnitOfWorkFactory,
    logs: RecentLogBuffer,
) -> OperationsFoundation:
    """Build Operations services and module-owned retention contributions."""
    incidents = IncidentService(units)
    job_runs = JobRunService(units, incidents)

    async def purge_incidents(
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with units() as work:
            removed = await IncidentRepository(work.session).purge_before(
                context.now - timedelta(days=RETENTION_DAYS),
                limit=context.batch_size,
            )
            await work.commit()
        return (cleanup_detail("operations", "Incidents", removed),)

    async def purge_job_runs(
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        removed = await job_runs.purge(context.now, context.batch_size)
        return (cleanup_detail("job-runs", "Job runs", removed),)

    return OperationsFoundation(
        incidents=incidents,
        job_runs=job_runs,
        logs=logs,
        housekeeping=(
            HousekeepingContribution(
                "operations",
                ("Incidents",),
                purge_incidents,
            ),
            HousekeepingContribution(
                "operations",
                ("Job runs",),
                purge_job_runs,
            ),
        ),
    )


def complete_operations_module(
    foundation: OperationsFoundation,
    jobs: tuple[JobDefinition, ...],
    housekeeping: tuple[HousekeepingContribution, ...],
) -> OperationsModule:
    """Complete Operations from public jobs and ordered cleanup contributions."""
    maintenance = HousekeepingMaintenance(housekeeping)
    coordinator = JobCoordinator(
        foundation.job_runs,
        (*jobs, maintenance.definition()),
    )
    return OperationsModule(
        incidents=foundation.incidents,
        job_runs=foundation.job_runs,
        jobs=coordinator,
        logs=foundation.logs,
        reconciliation_lifecycle=LifecycleResource(
            "job-runs.reconcile",
            start=foundation.job_runs.reconcile_interrupted_runs,
        ),
        jobs_lifecycle=LifecycleResource(
            "jobs",
            start=coordinator.start,
            close=coordinator.close,
        ),
    )

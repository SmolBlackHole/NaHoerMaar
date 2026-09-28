# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Operations module composition."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from nahoermaar.database.uow import UnitOfWork

from .incidents import IncidentRepository, IncidentService, RETENTION_DAYS
from .jobs import JobRunDetail, JobRunService
from .maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    HousekeepingMaintenance,
    cleanup_detail,
)
from .scheduler import JobCoordinator, JobDefinition

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class ModuleHousekeeping:
    """Named module contributions consumed by Operations."""

    users: HousekeepingContribution
    player: HousekeepingContribution
    catalog: HousekeepingContribution
    integrations: HousekeepingContribution


@dataclass(frozen=True, slots=True)
class OperationsServices:
    """Operations services needed before the runtime graph is complete."""

    incidents: IncidentService
    job_runs: JobRunService


@dataclass(frozen=True, slots=True)
class OperationsModule:
    """Fully composed Operations runtime."""

    incidents: IncidentService
    job_runs: JobRunService
    jobs: JobCoordinator


def create_operations_services(units: UnitOfWorkFactory) -> OperationsServices:
    """Build Operations services required by the message bus."""
    incidents = IncidentService(units)
    return OperationsServices(
        incidents,
        JobRunService(units, incidents),
    )


def create_operations_module(
    units: UnitOfWorkFactory,
    services: OperationsServices,
    jobs: tuple[JobDefinition, ...],
    housekeeping: ModuleHousekeeping,
) -> OperationsModule:
    """Compose one visible Housekeeping job from module-owned work."""

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
        removed = await services.job_runs.purge(context.now, context.batch_size)
        return (cleanup_detail("job-runs", "Job runs", removed),)

    maintenance = HousekeepingMaintenance(
        (
            housekeeping.users,
            housekeeping.player,
            housekeeping.catalog,
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
            housekeeping.integrations,
        )
    )
    return OperationsModule(
        services.incidents,
        services.job_runs,
        JobCoordinator(
            services.job_runs,
            (*jobs, maintenance.definition()),
        ),
    )

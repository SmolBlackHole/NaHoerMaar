# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Catalog module composition."""

from collections.abc import Callable
from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.operations.scheduler import JobDefinition

from .maintenance import CatalogHousekeeping, CatalogMaintenance
from .providers import CatalogProvider
from .service import CatalogService

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class CatalogModule:
    """Runtime dependencies exported by the Catalog module."""

    service: CatalogService
    jobs: tuple[JobDefinition, ...]
    housekeeping: HousekeepingContribution


def create_catalog_module(
    units: UnitOfWorkFactory,
    providers: tuple[CatalogProvider, ...],
) -> CatalogModule:
    """Build Catalog use cases and their module-owned bounded jobs."""
    service = CatalogService(units, providers)
    maintenance = CatalogMaintenance(units, service)
    housekeeping = CatalogHousekeeping(units)
    return CatalogModule(
        service,
        (maintenance.definition(),),
        housekeeping.contribution(),
    )

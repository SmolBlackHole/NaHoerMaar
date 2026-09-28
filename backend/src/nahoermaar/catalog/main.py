# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Catalog module composition."""

from collections.abc import Callable
from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.operations.scheduler import JobDefinition
from nahoermaar.views.catalog import CatalogCleanupReader

from .maintenance import (
    CatalogCleanup,
    CatalogHousekeeping,
    CatalogMaintenance,
    SourceRevalidation,
)
from .providers import CatalogProvider
from .service import CatalogService

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class CatalogModule:
    """Runtime dependencies exported by the Catalog module."""

    service: CatalogService
    jobs: tuple[JobDefinition, ...]
    housekeeping: HousekeepingContribution
    lifecycle: LifecycleResource


def create_catalog_module(
    units: UnitOfWorkFactory,
    providers: tuple[CatalogProvider, ...],
    cleanup_view: CatalogCleanupReader,
) -> CatalogModule:
    """Build Catalog use cases and their module-owned bounded jobs."""
    service = CatalogService(units, providers)
    maintenance = CatalogMaintenance(units, service)
    cleanup = CatalogCleanup(units, cleanup_view)
    revalidation = SourceRevalidation(units, service)
    housekeeping = CatalogHousekeeping(units)
    return CatalogModule(
        service,
        (
            maintenance.definition(),
            cleanup.definition(),
            revalidation.definition(),
        ),
        housekeeping.contribution(),
        LifecycleResource("catalog", close=service.close),
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Library module composition."""

from dataclasses import dataclass

from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.operations.scheduler import JobDefinition

from .maintenance import LibraryMaintenance, PlaylistSyncMaintenance
from .read_model import LibraryReadModel
from .service import LibraryService


@dataclass(frozen=True, slots=True)
class LibraryModule:
    """Use cases exported by the personal Library."""

    service: LibraryService
    jobs: tuple[JobDefinition, ...]
    housekeeping: HousekeepingContribution


def create_library_module(
    units: UnitOfWorkFactory,
    catalog: CatalogService,
) -> LibraryModule:
    """Build personal Library use cases and projections."""
    reader = LibraryReadModel(units)
    service = LibraryService(units, catalog, reader)
    return LibraryModule(
        service,
        (PlaylistSyncMaintenance(units, catalog).definition(),),
        LibraryMaintenance(units).contribution(),
    )

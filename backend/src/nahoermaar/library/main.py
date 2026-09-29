# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Library module composition."""

from dataclasses import dataclass

from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory

from .read_model import LibraryReadModel
from .service import LibraryService


@dataclass(frozen=True, slots=True)
class LibraryModule:
    """Use cases exported by the personal Library."""

    service: LibraryService


def create_library_module(
    units: UnitOfWorkFactory,
    catalog: CatalogService,
) -> LibraryModule:
    """Build personal Library use cases and projections."""
    reader = LibraryReadModel(units)
    return LibraryModule(LibraryService(units, catalog, reader))

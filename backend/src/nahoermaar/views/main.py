# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Composition boundary for cross-module read projections."""

from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.statistics.main import StatisticsModule

from .catalog import CatalogCleanupView
from .history import PlaybackHistoryView
from .profile import ProfileView


@dataclass(frozen=True, slots=True)
class ViewsModule:
    """Read-only projections exported to APIs and feature modules."""

    profiles: ProfileView
    history: PlaybackHistoryView
    catalog_cleanup: CatalogCleanupView


def create_views_module(
    units: UnitOfWorkFactory,
    statistics: StatisticsModule,
) -> ViewsModule:
    """Construct all cross-module read projections."""
    return ViewsModule(
        profiles=ProfileView(units, statistics.service),
        history=PlaybackHistoryView(units),
        catalog_cleanup=CatalogCleanupView(),
    )

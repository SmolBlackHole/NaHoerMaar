# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded cleanup owned by the Library module."""

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.operations.jobs import JobRunDetail
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)

from .repository import PlaylistRepository

_LABELS = ("Playlist undo receipts",)


class LibraryMaintenance:
    """Remove expired transient Library state."""

    __slots__ = ("_units",)

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution("library", _LABELS, self.run)

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with self._units() as work:
            removed = await PlaylistRepository(work.session).prune_entry_undos(
                context.now,
                limit=context.batch_size,
            )
            await work.commit()
        return (cleanup_detail("library", _LABELS[0], removed),)

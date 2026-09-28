# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded cleanup owned by the Player module."""

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)
from nahoermaar.operations.jobs import JobRunDetail

from .repository import SessionRepository

_LABELS = ("Queue undo records", "Operation receipts")


class PlayerMaintenance:
    """Remove expired transient Player state."""

    __slots__ = ("_units",)

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution("player", _LABELS, self.run)

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with self._units() as work:
            undos, receipts = await SessionRepository(work.session).prune(
                context.now,
                limit=context.batch_size,
            )
            await work.commit()
        return (
            cleanup_detail("player", _LABELS[0], undos),
            cleanup_detail("player", _LABELS[1], receipts),
        )

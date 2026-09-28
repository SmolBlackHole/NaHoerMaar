# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded cleanup owned by the Users module."""

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)
from nahoermaar.operations.jobs import JobRunDetail

from .repository import AuthRepository

_LABELS = ("Login attempts", "Browser sessions")


class UsersMaintenance:
    """Remove expired authentication state owned by Users."""

    __slots__ = ("_units",)

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution("users", _LABELS, self.run)

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with self._units() as work:
            attempts, sessions = await AuthRepository(work.session).prune(
                context.now,
                limit=context.batch_size,
            )
            await work.commit()
        return (
            cleanup_detail("users", _LABELS[0], attempts),
            cleanup_detail("users", _LABELS[1], sessions),
        )

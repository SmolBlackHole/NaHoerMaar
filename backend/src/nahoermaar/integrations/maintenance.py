# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded cleanup owned by external integrations."""

from datetime import timedelta

from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)
from nahoermaar.operations.jobs import JobRunDetail

from .avatars import DiscordAvatarStore

_LABELS = ("Discord avatars",)


class IntegrationsMaintenance:
    """Remove superseded cached integration assets."""

    __slots__ = ("_avatars",)

    def __init__(self, avatars: DiscordAvatarStore) -> None:
        self._avatars = avatars

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution("integrations", _LABELS, self.run)

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        removed = await self._avatars.prune(context.now - timedelta(days=7))
        return (cleanup_detail("integrations", _LABELS[0], removed),)

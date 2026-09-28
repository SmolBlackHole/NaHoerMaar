# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Player module composition."""

from collections.abc import Callable
from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import MessageBus
from nahoermaar.operations.maintenance import HousekeepingContribution

from .automation import PlaybackAutomation
from .maintenance import PlayerMaintenance
from .session import PlayerSessionManager, RadioResolver

type UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class PlayerModule:
    """Player runtime services and bounded maintenance."""

    service: PlayerSessionManager
    automation: PlaybackAutomation
    housekeeping: HousekeepingContribution


def create_player_module(
    units: UnitOfWorkFactory,
    bus: MessageBus,
    radio: RadioResolver,
    *,
    empty_channel_grace_seconds: float,
) -> PlayerModule:
    """Build the Player runtime from its injected boundaries."""
    service = PlayerSessionManager(units, bus, radio)
    return PlayerModule(
        service,
        PlaybackAutomation(
            service,
            bus,
            empty_channel_grace_seconds=empty_channel_grace_seconds,
        ),
        PlayerMaintenance(units).contribution(),
    )

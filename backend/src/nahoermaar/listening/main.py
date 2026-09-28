# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Listening module composition."""

from collections.abc import Callable
from dataclasses import dataclass

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.messaging import MessageBus
from nahoermaar.player.domain import ListeningSessionId
from nahoermaar.users.service import AccessService

from .service import (
    AdvancePlayback,
    BeginPlayback,
    DisconnectAudience,
    FinishPlayback,
    ListeningService,
    ObserveAudience,
)

type SessionIdSource = Callable[[], ListeningSessionId]


@dataclass(frozen=True, slots=True)
class ListeningModule:
    """Listening write service and its process lifecycle."""

    service: ListeningService
    lifecycle: LifecycleResource


def create_listening_module(
    units: UnitOfWorkFactory,
    bus: MessageBus,
    access: AccessService,
    session_id: SessionIdSource,
) -> ListeningModule:
    """Build Listening and bind its commands to the application bus."""
    service = ListeningService(units, bus, access)
    bus.register_command(BeginPlayback, service.begin)
    bus.register_command(AdvancePlayback, service.advance)
    bus.register_command(FinishPlayback, service.finish)
    bus.register_command(ObserveAudience, service.observe)
    bus.register_command(DisconnectAudience, service.disconnect)

    async def start() -> None:
        await service.start(session_id())

    return ListeningModule(
        service,
        LifecycleResource("listening", start=start, close=service.close),
    )

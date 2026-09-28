# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Player module composition and immutable completion stages."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid5

from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.listening.service import (
    AudienceChanged,
    AudienceUnavailable,
    ListeningService,
)
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.operations.incidents import IncidentService
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.users.service import AccessService, UserAccessChanged

from .automation import PlaybackAutomation
from .domain import OperationId, PlayerError, PlayerErrorCode, VoiceConnectionState
from .events import (
    AddTracks,
    ApplyRadioCandidates,
    CancelSleepTimer,
    CheckpointPlayback,
    ClearQueue,
    CompletePlayback,
    FailPlayback,
    JoinVoice,
    LeaveVoice,
    MoveQueueEntry,
    MutationReply,
    Pause,
    Play,
    PlaybackRuntimeChanged,
    PlayerChanged,
    PlayerCommand,
    RadioRefillRequested,
    RemoveQueueEntry,
    RetryRadio,
    Seek,
    SetPlayerSettings,
    SetSleepTimer,
    Skip,
    StartRadio,
    StopPlayback,
    StopRadio,
    SuspendPlayback,
    UndoQueue,
    VoiceConnectionChanged,
)
from .maintenance import PlayerMaintenance
from .playback import (
    PlaybackCoordinator,
    PlaybackPhase,
    PlaybackRuntimeState,
    PlaybackTransport,
)
from .read_model import PlayerReader
from .session import CatalogRadioResolver, PlayerSessionManager

type SummonHandler = Callable[[str, int, UUID], Awaitable[None]]

_COMMAND_TYPES: tuple[type[PlayerCommand], ...] = (
    AddTracks,
    RemoveQueueEntry,
    MoveQueueEntry,
    ClearQueue,
    UndoQueue,
    StartRadio,
    StopRadio,
    RetryRadio,
    ApplyRadioCandidates,
    Play,
    Pause,
    Skip,
    StopPlayback,
    Seek,
    SetPlayerSettings,
    SetSleepTimer,
    CancelSleepTimer,
    SuspendPlayback,
    JoinVoice,
    LeaveVoice,
    CompletePlayback,
    FailPlayback,
    CheckpointPlayback,
)


@dataclass(frozen=True, slots=True)
class PlayerPreparation:
    """Player core ready for Listening and transport injection."""

    service: PlayerSessionManager
    automation: PlaybackAutomation
    users: AccessService
    housekeeping: HousekeepingContribution
    summon: SummonHandler
    session_lifecycle: LifecycleResource
    automation_lifecycle: LifecycleResource


@dataclass(frozen=True, slots=True)
class PlayerModule:
    """Completed Player runtime and all of its lifecycle resources."""

    service: PlayerSessionManager
    reader: PlayerReader
    automation: PlaybackAutomation
    playback: PlaybackCoordinator | None
    housekeeping: HousekeepingContribution
    summon: SummonHandler
    session_lifecycle: LifecycleResource
    automation_lifecycle: LifecycleResource
    playback_lifecycle: LifecycleResource | None


def prepare_player_module(
    units: UnitOfWorkFactory,
    bus: MessageBus,
    catalog: CatalogService,
    access: AccessService,
    *,
    empty_channel_grace_seconds: float,
) -> PlayerPreparation:
    """Build the Player core and bind everything independent of audio output."""
    service = PlayerSessionManager(units, bus, CatalogRadioResolver(catalog))
    automation = PlaybackAutomation(
        service,
        bus,
        empty_channel_grace_seconds=empty_channel_grace_seconds,
    )

    async def execute(
        command: PlayerCommand,
        context: MessageContext,
    ) -> MutationReply:
        return await service.execute(command, context)

    for command_type in _COMMAND_TYPES:
        bus.register_command(command_type, execute)

    async def reauthenticate_stream(
        _event: UserAccessChanged,
        _context: MessageContext,
    ) -> None:
        service.events.reauthenticate()

    bus.subscribe(PlayerChanged, service.broadcast)
    bus.subscribe(PlayerChanged, automation.player_changed)
    bus.subscribe(PlaybackRuntimeChanged, service.broadcast_runtime)
    bus.subscribe(VoiceConnectionChanged, service.broadcast_runtime)
    bus.subscribe(RadioRefillRequested, service.refill)
    bus.subscribe(UserAccessChanged, reauthenticate_stream)
    bus.subscribe(AudienceChanged, automation.audience_changed)
    bus.subscribe(AudienceUnavailable, automation.audience_unavailable)

    async def summon(
        discord_id: str,
        channel_id: int,
        correlation_id: UUID,
    ) -> None:
        user = await access.require_discord_access(discord_id)
        context = MessageContext(correlation_id=correlation_id, actor_id=user.id)
        session_id = service.state.session.id
        await bus.execute(
            JoinVoice(
                session_id,
                OperationId(uuid5(correlation_id, "join-voice")),
                channel_id,
            ),
            context,
        )
        state = service.state
        if state.checkpoint.request is None and not state.queue.entries:
            return
        try:
            await bus.execute(
                Play(
                    session_id,
                    OperationId(uuid5(correlation_id, "play")),
                ),
                context,
            )
        except PlayerError as error:
            if error.code is not PlayerErrorCode.NOTHING_TO_PLAY:
                raise

    return PlayerPreparation(
        service=service,
        automation=automation,
        users=access,
        housekeeping=PlayerMaintenance(units).contribution(),
        summon=summon,
        session_lifecycle=LifecycleResource(
            "player",
            start=service.start,
            close=service.close,
        ),
        automation_lifecycle=LifecycleResource(
            "automation",
            start=automation.start,
            close=automation.close,
        ),
    )


def complete_player_module(
    preparation: PlayerPreparation,
    catalog: CatalogService,
    listening: ListeningService,
    bus: MessageBus,
    transport: PlaybackTransport | None,
    *,
    incidents: IncidentService,
) -> PlayerModule:
    """Complete Player with the dependencies that require its prepared core."""
    playback = (
        PlaybackCoordinator(
            preparation.service,
            catalog,
            listening,
            bus,
            transport,
            incidents=incidents,
        )
        if transport is not None
        else None
    )
    if playback is not None:
        bus.subscribe(PlayerChanged, playback.player_changed)

    disabled_runtime = PlaybackRuntimeState(
        PlaybackPhase.DISABLED,
        None,
        None,
        None,
        0.0,
        None,
        None,
        VoiceConnectionState(),
        None,
    )
    reader = PlayerReader(
        catalog,
        preparation.users,
        lambda: playback.status if playback is not None else disabled_runtime,
    )

    return PlayerModule(
        service=preparation.service,
        reader=reader,
        automation=preparation.automation,
        playback=playback,
        housekeeping=preparation.housekeeping,
        summon=preparation.summon,
        session_lifecycle=preparation.session_lifecycle,
        automation_lifecycle=preparation.automation_lifecycle,
        playback_lifecycle=(
            LifecycleResource(
                "playback",
                start=playback.start,
                close=playback.close,
            )
            if playback is not None
            else None
        ),
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated player queries and serialized queue and radio commands."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import (
    DiscoverySnapshotId,
    MediaKind,
    Track,
    TrackId,
    TrackSourceId,
)
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.messaging import MessageContext
from nahoermaar.player.domain import (
    ListeningSessionId,
    OperationId,
    PlayerState,
    QueueEntryId,
    RadioSeed,
    TrackRequest,
    UndoId,
)
from nahoermaar.player.events import (
    AddTracks,
    CancelSleepTimer,
    ClearQueue,
    JoinVoice,
    LeaveVoice,
    MoveQueueEntry,
    MutationReply,
    Pause,
    Play,
    PlayerCommand,
    RemoveQueueEntry,
    Seek,
    SetPlayerSettings,
    SetSleepTimer,
    Skip,
    RetryRadio,
    StartRadio,
    StopPlayback,
    StopRadio,
    TrackSelection,
    UndoQueue,
)
from nahoermaar.player.playback import PlaybackPhase
from nahoermaar.player.read_model import PlayerReadModel
from nahoermaar.users.domain import User, UserId

from .catalog import TrackView, track_view
from .errors import error_responses
from .middleware import authenticated


class View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerRuntimeErrorCode(StrEnum):
    """Stable public reasons for runtime failures with details kept in logs."""

    PLAYBACK_FAILED = "playback_failed"
    RADIO_PROVIDER_FAILED = "radio_provider_failed"
    VOICE_CONNECTION_FAILED = "voice_connection_failed"


class TrackSelectionInput(View):
    track_id: UUID
    source_id: UUID | None = None


class AddQueueInput(View):
    tracks: tuple[TrackSelectionInput, ...] = Field(min_length=1, max_length=100)
    skip_duplicates: bool = False


class RevisionInput(View):
    expected_queue_revision: int = Field(ge=0)


class MoveQueueInput(RevisionInput):
    before_entry_id: UUID | None = None


class ClearQueueInput(RevisionInput):
    requested_by: UUID | None = None


class UndoQueueInput(View):
    undo_id: UUID


class RadioSeedInput(View):
    kind: MediaKind
    track_source_id: UUID | None = None
    discovery_snapshot_id: UUID | None = None


class StartRadioInput(View):
    seed: RadioSeedInput
    expected_generation: UUID | None = None


class RadioMutationInput(View):
    expected_generation: UUID


class ControlAction(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    SKIP = "skip"
    STOP = "stop"
    SEEK = "seek"


class ControlInput(View):
    action: ControlAction
    seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action is ControlAction.SEEK and self.seconds is None:
            raise ValueError("Seek requires seconds.")
        if self.action is not ControlAction.SEEK and self.seconds is not None:
            raise ValueError("Only seek accepts seconds.")
        return self


class PlayerSettingsInput(View):
    volume: float | None = Field(default=None, ge=0, le=1)
    crossfade_seconds: int | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if self.volume is None and self.crossfade_seconds is None:
            raise ValueError("At least one Player setting is required.")
        if self.crossfade_seconds is not None and self.crossfade_seconds not in {
            0,
            3,
            4,
            5,
            6,
            7,
        }:
            raise ValueError("Crossfade must be disabled or between 3 and 7 seconds.")
        return self


class VoiceInput(View):
    channel_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class SleepTimerInput(View):
    seconds: int = Field(ge=60, le=86_400)


IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]


class VoiceChannelView(View):
    id: str
    name: str
    guild_id: str
    guild_name: str
    can_connect: bool
    can_speak: bool


class ContributorView(View):
    user_id: UUID
    display_name: str
    discord_id: str
    discord_username: str | None
    avatar_url: str


class RequestView(View):
    id: UUID
    origin: str
    requested_at: datetime
    requested_by: UUID
    radio_run_id: UUID | None
    source_id: UUID | None
    contributor: ContributorView | None
    track: TrackView


class QueueEntryView(View):
    id: UUID
    position: int
    request: RequestView


class CheckpointView(View):
    intent: str
    position_seconds: float
    request_id: UUID | None


class RadioView(View):
    id: UUID
    generation: UUID
    state: str
    initiated_by: UUID
    started_at: datetime
    seed_kind: str
    seed_track_source_id: UUID | None
    seed_discovery_snapshot_id: UUID | None
    seed_title: str | None
    seed_track: TrackView | None
    continuation: str | None
    error_code: PlayerRuntimeErrorCode | None
    initiator: ContributorView | None


class VoiceRuntimeView(View):
    phase: str
    channel_id: str | None
    attempt: int
    error_code: PlayerRuntimeErrorCode | None


class PlaybackRuntimeView(View):
    phase: str
    current: RequestView | None
    playback_id: UUID | None
    attempt_id: UUID | None
    position_seconds: float
    position_updated_at: datetime | None
    duration_seconds: float | None
    voice: VoiceRuntimeView
    last_error_code: PlayerRuntimeErrorCode | None


class PlayerView(View):
    session_id: UUID
    revision: int
    queue_revision: int
    channel_id: str | None
    volume: float
    crossfade_seconds: int
    sleep_timer_expires_at: datetime | None
    queue: tuple[QueueEntryView, ...]
    checkpoint: CheckpointView
    radio: RadioView | None
    runtime: PlaybackRuntimeView


class OutcomeView(View):
    action: str
    added_count: int
    removed_count: int
    restored_count: int
    skipped_count: int
    entry_ids: tuple[UUID, ...]
    undo_id: UUID | None
    undo_expires_at: datetime | None


class MutationView(View):
    operation_id: UUID
    player: PlayerView
    outcome: OutcomeView
    replayed: bool


_READ_ERRORS = error_responses(401, 500, 503)
_MUTATION_ERRORS = error_responses(401, 403, 409, 422, 500, 503)
_RESOURCE_MUTATION_ERRORS = error_responses(401, 403, 404, 409, 422, 500, 503)


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/player", tags=["player"])

    @routes.get("", operation_id="getPlayer", responses=_READ_ERRORS)
    async def player() -> PlayerView:
        return await player_view(application, application.player.service.state)

    @routes.post(
        "/queue",
        operation_id="addQueueEntries",
        responses=_RESOURCE_MUTATION_ERRORS,
    )
    async def add(
        request: Request,
        body: AddQueueInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            AddTracks(
                _session_id(application),
                OperationId(idempotency_key),
                tuple(
                    TrackSelection(
                        TrackId(item.track_id),
                        TrackSourceId(item.source_id)
                        if item.source_id is not None
                        else None,
                    )
                    for item in body.tracks
                ),
                body.skip_duplicates,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.delete(
        "/queue/{entry_id}",
        operation_id="removeQueueEntry",
        responses=_RESOURCE_MUTATION_ERRORS,
    )
    async def remove(
        entry_id: UUID,
        request: Request,
        body: RevisionInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            RemoveQueueEntry(
                _session_id(application),
                OperationId(idempotency_key),
                QueueEntryId(entry_id),
                body.expected_queue_revision,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.patch(
        "/queue/{entry_id}",
        operation_id="moveQueueEntry",
        responses=_RESOURCE_MUTATION_ERRORS,
    )
    async def move(
        entry_id: UUID,
        request: Request,
        body: MoveQueueInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            MoveQueueEntry(
                _session_id(application),
                OperationId(idempotency_key),
                QueueEntryId(entry_id),
                (
                    QueueEntryId(body.before_entry_id)
                    if body.before_entry_id is not None
                    else None
                ),
                body.expected_queue_revision,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post(
        "/queue/clear",
        operation_id="clearQueue",
        responses=_MUTATION_ERRORS,
    )
    async def clear(
        request: Request,
        body: ClearQueueInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            ClearQueue(
                _session_id(application),
                OperationId(idempotency_key),
                body.expected_queue_revision,
                UserId(body.requested_by) if body.requested_by is not None else None,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post(
        "/queue/undo",
        operation_id="undoQueue",
        responses=_MUTATION_ERRORS,
    )
    async def undo(
        request: Request,
        body: UndoQueueInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            UndoQueue(
                _session_id(application),
                OperationId(idempotency_key),
                UndoId(body.undo_id),
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post(
        "/control",
        operation_id="controlPlayer",
        responses=_MUTATION_ERRORS,
    )
    async def control(
        request: Request,
        body: ControlInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        session_id = _session_id(application)
        operation_id = OperationId(idempotency_key)
        if body.action is ControlAction.PLAY:
            command: PlayerCommand = Play(session_id, operation_id)
        elif body.action is ControlAction.PAUSE:
            command = Pause(session_id, operation_id)
        elif body.action is ControlAction.SKIP:
            command = Skip(session_id, operation_id)
        elif body.action is ControlAction.STOP:
            command = StopPlayback(session_id, operation_id)
        else:
            command = Seek(session_id, operation_id, cast(float, body.seconds))
        return await execute(request, command)

    @routes.patch("", operation_id="updatePlayer", responses=_MUTATION_ERRORS)
    async def update_player(
        request: Request,
        body: PlayerSettingsInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        return await execute(
            request,
            SetPlayerSettings(
                _session_id(application),
                OperationId(idempotency_key),
                volume=body.volume,
                crossfade_seconds=body.crossfade_seconds,
            ),
        )

    @routes.put(
        "/sleep-timer",
        operation_id="setSleepTimer",
        responses=_MUTATION_ERRORS,
    )
    async def set_sleep_timer(
        request: Request,
        body: SleepTimerInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        return await execute(
            request,
            SetSleepTimer(
                _session_id(application),
                OperationId(idempotency_key),
                datetime.now(UTC) + timedelta(seconds=body.seconds),
            ),
        )

    @routes.delete(
        "/sleep-timer",
        operation_id="cancelSleepTimer",
        responses=_MUTATION_ERRORS,
    )
    async def cancel_sleep_timer(
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        return await execute(
            request,
            CancelSleepTimer(
                _session_id(application),
                OperationId(idempotency_key),
            ),
        )

    @routes.get(
        "/voice/channels",
        operation_id="listVoiceChannels",
        responses=_READ_ERRORS,
    )
    async def voice_channels() -> tuple[VoiceChannelView, ...]:
        if application.player.playback is None:
            return ()
        return tuple(
            VoiceChannelView(
                id=str(channel.id),
                name=channel.name,
                guild_id=str(channel.guild_id),
                guild_name=channel.guild_name,
                can_connect=channel.can_connect,
                can_speak=channel.can_speak,
            )
            for channel in application.player.playback.channels()
        )

    @routes.put("/voice", operation_id="joinVoice", responses=_MUTATION_ERRORS)
    async def join_voice(
        request: Request,
        body: VoiceInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        return await execute(
            request,
            JoinVoice(
                _session_id(application),
                OperationId(idempotency_key),
                int(body.channel_id),
            ),
        )

    @routes.delete("/voice", operation_id="leaveVoice", responses=_MUTATION_ERRORS)
    async def leave_voice(
        request: Request,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        return await execute(
            request,
            LeaveVoice(_session_id(application), OperationId(idempotency_key)),
        )

    @routes.put(
        "/radio",
        operation_id="startRadio",
        responses=_RESOURCE_MUTATION_ERRORS,
    )
    async def start_radio(
        request: Request,
        body: StartRadioInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        seed = RadioSeed(
            body.seed.kind,
            (
                TrackSourceId(body.seed.track_source_id)
                if body.seed.track_source_id is not None
                else None
            ),
            (
                DiscoverySnapshotId(body.seed.discovery_snapshot_id)
                if body.seed.discovery_snapshot_id is not None
                else None
            ),
        )
        result = await application.bus.execute(
            StartRadio(
                _session_id(application),
                OperationId(idempotency_key),
                seed,
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.delete("/radio", operation_id="stopRadio", responses=_MUTATION_ERRORS)
    async def stop_radio(
        request: Request,
        body: RadioMutationInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            StopRadio(
                _session_id(application),
                OperationId(idempotency_key),
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post(
        "/radio/retry",
        operation_id="retryRadio",
        responses=_MUTATION_ERRORS,
    )
    async def retry_radio(
        request: Request,
        body: RadioMutationInput,
        idempotency_key: IdempotencyKey,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            RetryRadio(
                _session_id(application),
                OperationId(idempotency_key),
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    async def execute(request: Request, command: PlayerCommand) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            command,
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    return routes


def _session_id(application: Application) -> ListeningSessionId:
    return application.player.service.state.session.id


async def _mutation(
    application: Application,
    reply: MutationReply,
) -> MutationView:
    outcome = reply.outcome
    return MutationView(
        operation_id=reply.operation_id,
        player=await player_view(application, reply.state),
        outcome=OutcomeView(
            action=outcome.action.value,
            added_count=outcome.added_count,
            removed_count=outcome.removed_count,
            restored_count=outcome.restored_count,
            skipped_count=outcome.skipped_count,
            entry_ids=outcome.entry_ids,
            undo_id=outcome.undo_id,
            undo_expires_at=outcome.undo_expires_at,
        ),
        replayed=reply.replayed,
    )


async def player_view(
    application: Application,
    state: PlayerState,
) -> PlayerView:
    projection = await application.player.reader.read(state)
    return player_document(projection, application.integrations.avatars)


def player_document(
    projection: PlayerReadModel,
    avatars: DiscordAvatarStore,
) -> PlayerView:
    state = projection.state
    runtime = projection.runtime
    current_request = projection.current_request
    tracks = projection.tracks
    contributors = projection.contributors
    radio_seed_track = projection.radio_seed_track
    radio_seed_title = projection.radio_seed_title
    queue = tuple(
        QueueEntryView(
            id=entry.id,
            position=entry.position,
            request=_request_view(entry.request, tracks, contributors, avatars),
        )
        for entry in state.queue.entries
    )
    run = state.radio if state.radio is not None and state.radio.active else None
    runtime_matches_current = (
        current_request is not None
        and runtime.request is not None
        and runtime.request.id == current_request.id
    )
    runtime_phase = (
        runtime.phase
        if current_request is None or runtime_matches_current
        else PlaybackPhase.STARTING
    )
    return PlayerView(
        session_id=state.session.id,
        revision=state.session.revision,
        queue_revision=state.session.queue_revision,
        channel_id=(
            str(state.session.channel_id)
            if state.session.channel_id is not None
            else None
        ),
        volume=state.session.volume,
        crossfade_seconds=state.session.crossfade_seconds,
        sleep_timer_expires_at=state.session.sleep_at,
        queue=queue,
        checkpoint=CheckpointView(
            intent=state.checkpoint.intent.value,
            position_seconds=state.checkpoint.position_seconds,
            request_id=(
                state.checkpoint.request.id
                if state.checkpoint.request is not None
                else None
            ),
        ),
        radio=(
            RadioView(
                id=run.id,
                generation=run.generation,
                state=run.state.value,
                initiated_by=run.initiated_by,
                started_at=run.started_at,
                seed_kind=run.seed.kind.value,
                seed_track_source_id=run.seed.track_source_id,
                seed_discovery_snapshot_id=run.seed.discovery_snapshot_id,
                seed_title=radio_seed_title,
                seed_track=(
                    track_view(radio_seed_track)
                    if radio_seed_track is not None
                    else None
                ),
                continuation=run.continuation,
                error_code=(
                    PlayerRuntimeErrorCode.RADIO_PROVIDER_FAILED
                    if run.error is not None
                    else None
                ),
                initiator=_contributor_view(
                    contributors.get(run.initiated_by),
                    avatars,
                ),
            )
            if run is not None
            else None
        ),
        runtime=PlaybackRuntimeView(
            phase=runtime_phase.value,
            current=(
                _request_view(current_request, tracks, contributors, avatars)
                if current_request is not None
                else None
            ),
            playback_id=runtime.playback_id if runtime_matches_current else None,
            attempt_id=runtime.attempt_id if runtime_matches_current else None,
            position_seconds=(
                runtime.position_seconds
                if runtime_matches_current
                else state.checkpoint.position_seconds
            ),
            position_updated_at=(
                runtime.position_updated_at if runtime_matches_current else None
            ),
            duration_seconds=(
                runtime.duration_seconds if runtime_matches_current else None
            ),
            voice=VoiceRuntimeView(
                phase=runtime.voice.phase.value,
                channel_id=(
                    str(runtime.voice.channel_id)
                    if runtime.voice.channel_id is not None
                    else None
                ),
                attempt=runtime.voice.attempt,
                error_code=(
                    PlayerRuntimeErrorCode.VOICE_CONNECTION_FAILED
                    if runtime.voice.error is not None
                    else None
                ),
            ),
            last_error_code=(
                PlayerRuntimeErrorCode.PLAYBACK_FAILED
                if runtime.last_error is not None
                else None
            ),
        ),
    )


def _request_view(
    request: TrackRequest,
    tracks: Mapping[TrackId, Track],
    contributors: Mapping[UserId, User],
    avatars: DiscordAvatarStore,
) -> RequestView:
    track = tracks.get(request.track_id)
    if track is None:
        raise RuntimeError(
            f"Track is missing from player projection: {request.track_id}"
        )
    return RequestView(
        id=request.id,
        origin=request.origin.value,
        requested_at=request.requested_at,
        requested_by=request.requested_by,
        radio_run_id=request.radio_run_id,
        source_id=request.source_id,
        contributor=_contributor_view(
            contributors.get(request.requested_by),
            avatars,
        ),
        track=track_view(track),
    )


def _contributor_view(
    user: User | None,
    avatars: DiscordAvatarStore,
) -> ContributorView | None:
    if user is None:
        return None
    return ContributorView(
        user_id=user.id,
        display_name=(
            user.profile.display_name
            or user.discord.username
            or f"Listener {str(user.id)[:8]}"
        ),
        discord_id=user.discord.discord_id,
        discord_username=user.discord.username,
        avatar_url=avatars.public_url(
            user.discord.discord_id,
            avatar_hash=user.discord.avatar_hash,
        ),
    )

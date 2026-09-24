# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated player queries and serialized queue and radio commands."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import (
    DiscoverySnapshotId,
    MediaKind,
    TrackId,
    TrackSourceId,
)
from nahoermaar.messaging import MessageContext
from nahoermaar.player.domain import (
    ListeningSessionId,
    OperationId,
    QueueEntryId,
    RadioSeed,
    UndoId,
)
from nahoermaar.player.events import (
    AddTracks,
    ClearQueue,
    MoveQueueEntry,
    MutationReply,
    RemoveQueueEntry,
    RetryRadio,
    StartRadio,
    StopRadio,
    TrackSelection,
    UndoQueue,
)

from .catalog import TrackView, track_view
from .middleware import authenticated


class View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrackSelectionInput(View):
    track_id: UUID
    source_id: UUID | None = None


class AddQueueInput(View):
    operation_id: UUID
    tracks: tuple[TrackSelectionInput, ...] = Field(min_length=1, max_length=100)
    skip_duplicates: bool = False


class RevisionInput(View):
    operation_id: UUID
    expected_queue_revision: int = Field(ge=0)


class MoveQueueInput(RevisionInput):
    before_entry_id: UUID | None = None


class ClearQueueInput(RevisionInput):
    only_mine: bool = False


class UndoQueueInput(View):
    operation_id: UUID
    undo_id: UUID


class RadioSeedInput(View):
    kind: MediaKind
    track_source_id: UUID | None = None
    discovery_snapshot_id: UUID | None = None


class StartRadioInput(View):
    operation_id: UUID
    seed: RadioSeedInput
    expected_generation: UUID | None = None


class RadioMutationInput(View):
    operation_id: UUID
    expected_generation: UUID


class RequestView(View):
    id: UUID
    origin: str
    requested_at: datetime
    requested_by: UUID | None
    radio_run_id: UUID | None
    source_id: UUID | None
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
    continuation: str | None
    error: str | None


class PlayerView(View):
    session_id: UUID
    revision: int
    queue_revision: int
    channel_id: int | None
    volume: float
    crossfade_seconds: int
    queue: tuple[QueueEntryView, ...]
    checkpoint: CheckpointView
    radio: RadioView | None


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
    player: PlayerView
    outcome: OutcomeView
    replayed: bool


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/player", tags=["player"])

    @routes.get("")
    async def player() -> PlayerView:
        return await _player(application, application.player.state)

    @routes.post("/queue")
    async def add(request: Request, body: AddQueueInput) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            AddTracks(
                _session_id(application),
                OperationId(body.operation_id),
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

    @routes.delete("/queue/{entry_id}")
    async def remove(
        entry_id: UUID,
        request: Request,
        body: RevisionInput,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            RemoveQueueEntry(
                _session_id(application),
                OperationId(body.operation_id),
                QueueEntryId(entry_id),
                body.expected_queue_revision,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.put("/queue/{entry_id}")
    async def move(
        entry_id: UUID,
        request: Request,
        body: MoveQueueInput,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            MoveQueueEntry(
                _session_id(application),
                OperationId(body.operation_id),
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

    @routes.post("/queue/clear")
    async def clear(request: Request, body: ClearQueueInput) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            ClearQueue(
                _session_id(application),
                OperationId(body.operation_id),
                body.expected_queue_revision,
                current.user.id if body.only_mine else None,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post("/queue/undo")
    async def undo(request: Request, body: UndoQueueInput) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            UndoQueue(
                _session_id(application),
                OperationId(body.operation_id),
                UndoId(body.undo_id),
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post("/radio")
    async def start_radio(request: Request, body: StartRadioInput) -> MutationView:
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
                OperationId(body.operation_id),
                seed,
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post("/radio/stop")
    async def stop_radio(
        request: Request,
        body: RadioMutationInput,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            StopRadio(
                _session_id(application),
                OperationId(body.operation_id),
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    @routes.post("/radio/retry")
    async def retry_radio(
        request: Request,
        body: RadioMutationInput,
    ) -> MutationView:
        current = authenticated(request)
        result = await application.bus.execute(
            RetryRadio(
                _session_id(application),
                OperationId(body.operation_id),
                body.expected_generation,
            ),
            MessageContext(actor_id=current.user.id),
        )
        return await _mutation(application, result)

    return routes


def _session_id(application: Application) -> ListeningSessionId:
    return application.player.state.session.id


async def _mutation(
    application: Application,
    reply: MutationReply,
) -> MutationView:
    outcome = reply.outcome
    return MutationView(
        player=await _player(application, reply.state),
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


async def _player(
    application: Application,
    state: object,
) -> PlayerView:
    from nahoermaar.player.domain import PlayerState

    if not isinstance(state, PlayerState):
        raise TypeError("Player view requires a PlayerState.")
    tracks = await application.catalog.tracks(
        {entry.track_id for entry in state.queue.entries}
    )
    queue = tuple(
        QueueEntryView(
            id=entry.id,
            position=entry.position,
            request=RequestView(
                id=entry.request.id,
                origin=entry.request.origin.value,
                requested_at=entry.request.requested_at,
                requested_by=entry.request.requested_by,
                radio_run_id=entry.request.radio_run_id,
                source_id=entry.request.source_id,
                track=track_view(tracks[entry.track_id]),
            ),
        )
        for entry in state.queue.entries
    )
    run = state.radio if state.radio is not None and state.radio.active else None
    return PlayerView(
        session_id=state.session.id,
        revision=state.session.revision,
        queue_revision=state.session.queue_revision,
        channel_id=state.session.channel_id,
        volume=state.session.volume,
        crossfade_seconds=state.session.crossfade_seconds,
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
                continuation=run.continuation,
                error=run.error,
            )
            if run is not None
            else None
        ),
    )

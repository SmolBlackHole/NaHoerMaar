# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Pure queue and radio transitions for one listening session."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from nahoermaar.users.domain import UserId

from .domain import (
    RADIO_TARGET,
    UNDO_LIFETIME,
    ListeningSessionId,
    MutationOutcome,
    PlayerAction,
    PlayerError,
    PlayerErrorCode,
    PlayerState,
    Queue,
    QueueEntry,
    QueueEntryId,
    QueueUndo,
    RadioCandidate,
    RadioCandidateId,
    RadioRun,
    RadioRunId,
    RadioState,
    RequestOrigin,
    UndoGroup,
    UndoId,
    new_request,
)
from .events import (
    AddTracks,
    ApplyRadioCandidates,
    ClearQueue,
    MoveQueueEntry,
    PlayerChanged,
    PlayerCommand,
    PlayerEvent,
    QueueChanged,
    RadioRefillRequested,
    RemoveQueueEntry,
    RetryRadio,
    StartRadio,
    StopRadio,
    UndoQueue,
)


@dataclass(frozen=True, slots=True)
class Transition:
    state: PlayerState
    outcome: MutationOutcome
    events: tuple[PlayerEvent, ...] = ()
    save_undo: QueueUndo | None = None
    consume_undo: UndoId | None = None


def transition(
    state: PlayerState,
    command: PlayerCommand,
    actor_id: UserId | None,
    now: datetime,
    *,
    undo: QueueUndo | None = None,
) -> Transition:
    """Apply one command without performing I/O."""
    if now.utcoffset() is None:
        raise ValueError("Transition time must be timezone-aware.")

    if isinstance(command, ApplyRadioCandidates):
        result = _apply_radio(state, command, now)
    else:
        actor = _actor(actor_id)
        if isinstance(command, AddTracks):
            result = _add(state, command, actor, now)
        elif isinstance(command, RemoveQueueEntry):
            result = _remove(state, command, actor, now)
        elif isinstance(command, MoveQueueEntry):
            result = _move(state, command, now)
        elif isinstance(command, ClearQueue):
            result = _clear(state, command, actor, now)
        elif isinstance(command, UndoQueue):
            result = _undo(state, command, actor, now, undo)
        elif isinstance(command, StartRadio):
            result = _start_radio(state, command, actor, now)
        elif isinstance(command, StopRadio):
            result = _stop_radio(state, command, now)
        else:
            result = _retry_radio(state, command, now)

    if result.state == state:
        return result

    queue_changed = result.state.queue != state.queue
    session = replace(
        result.state.session,
        revision=state.session.revision + 1,
        queue_revision=state.session.queue_revision + int(queue_changed),
        updated_at=now,
    )
    queue = replace(result.state.queue, revision=session.queue_revision)
    updated = replace(result.state, session=session, queue=queue)
    events = list(result.events)
    if queue_changed:
        events.append(QueueChanged(session.id, session.queue_revision))
    events.append(
        PlayerChanged(session.id, session.revision, result.outcome.action.value)
    )
    return replace(result, state=updated, events=tuple(events))


def _actor(actor_id: UserId | None) -> UserId:
    if actor_id is None:
        raise PlayerError(PlayerErrorCode.ACTOR_REQUIRED, 401)
    return actor_id


def _add(
    state: PlayerState,
    command: AddTracks,
    actor_id: UserId,
    now: datetime,
) -> Transition:
    if not command.selections:
        raise PlayerError(PlayerErrorCode.INVALID_COMMAND)
    existing = {entry.track_id for entry in state.queue.entries}
    accepted: list[QueueEntry] = []
    skipped = 0
    for selection in command.selections:
        if command.skip_duplicates and selection.track_id in existing:
            skipped += 1
            continue
        request = new_request(
            state.session.id,
            selection.track_id,
            selection.source_id,
            now,
            actor_id=actor_id,
        )
        accepted.append(
            QueueEntry(
                QueueEntryId(uuid4()),
                state.session.id,
                request,
                0,
            )
        )
        existing.add(selection.track_id)

    manual = [
        entry
        for entry in state.queue.entries
        if entry.request.origin is RequestOrigin.MANUAL
    ]
    radio = [
        entry
        for entry in state.queue.entries
        if entry.request.origin is RequestOrigin.RADIO
    ]
    queue = _queue(state.queue, (*manual, *accepted, *radio))
    outcome = MutationOutcome(
        PlayerAction.QUEUE_ADDED,
        added_count=len(accepted),
        skipped_count=skipped,
        entry_ids=tuple(entry.id for entry in accepted),
    )
    return Transition(replace(state, queue=queue), outcome)


def _remove(
    state: PlayerState,
    command: RemoveQueueEntry,
    actor_id: UserId,
    now: datetime,
) -> Transition:
    _queue_revision(state, command.expected_queue_revision)
    index = _entry_index(state.queue, command.entry_id)
    entry = state.queue.entries[index]
    remaining = state.queue.entries[:index] + state.queue.entries[index + 1 :]
    undo = _undo_record(
        state.session.id,
        actor_id,
        now,
        (
            UndoGroup(
                (entry,), _id_before(state.queue, index), _id_after(state.queue, index)
            ),
        ),
    )
    updated = replace(state, queue=_queue(state.queue, remaining))
    updated, events = _maintain_radio(updated, now)
    outcome = MutationOutcome(
        PlayerAction.QUEUE_REMOVED,
        removed_count=1,
        entry_ids=(entry.id,),
        undo_id=undo.id,
        undo_expires_at=undo.expires_at,
    )
    return Transition(updated, outcome, events, save_undo=undo)


def _move(
    state: PlayerState,
    command: MoveQueueEntry,
    now: datetime,
) -> Transition:
    _queue_revision(state, command.expected_queue_revision)
    source = _entry_index(state.queue, command.entry_id)
    entries = list(state.queue.entries)
    entry = entries.pop(source)
    if command.before_entry_id is None:
        target = len(entries)
    else:
        try:
            target = next(
                index
                for index, candidate in enumerate(entries)
                if candidate.id == command.before_entry_id
            )
        except StopIteration as error:
            raise PlayerError(PlayerErrorCode.QUEUE_ENTRY_NOT_FOUND, 404) from error
    entries.insert(target, entry)
    queue = _queue(state.queue, tuple(entries))
    outcome = MutationOutcome(PlayerAction.QUEUE_MOVED, entry_ids=(entry.id,))
    return Transition(replace(state, queue=queue), outcome)


def _clear(
    state: PlayerState,
    command: ClearQueue,
    actor_id: UserId,
    now: datetime,
) -> Transition:
    _queue_revision(state, command.expected_queue_revision)
    selected = tuple(
        entry
        for entry in state.queue.entries
        if command.requested_by is None
        or entry.request.requested_by == command.requested_by
    )
    if not selected:
        return Transition(state, MutationOutcome(PlayerAction.QUEUE_CLEARED))
    selected_ids = {entry.id for entry in selected}
    groups = _groups(state.queue, selected_ids)
    remaining = tuple(
        entry for entry in state.queue.entries if entry.id not in selected_ids
    )
    undo = _undo_record(state.session.id, actor_id, now, groups)
    updated = replace(state, queue=_queue(state.queue, remaining))
    updated, events = _maintain_radio(updated, now)
    outcome = MutationOutcome(
        PlayerAction.QUEUE_CLEARED,
        removed_count=len(selected),
        entry_ids=tuple(entry.id for entry in selected),
        undo_id=undo.id,
        undo_expires_at=undo.expires_at,
    )
    return Transition(updated, outcome, events, save_undo=undo)


def _undo(
    state: PlayerState,
    command: UndoQueue,
    actor_id: UserId,
    now: datetime,
    undo: QueueUndo | None,
) -> Transition:
    if (
        undo is None
        or undo.id != command.undo_id
        or undo.session_id != state.session.id
        or undo.actor_id != actor_id
        or undo.expires_at <= now
    ):
        raise PlayerError(PlayerErrorCode.UNDO_UNAVAILABLE, 409)

    entries = list(state.queue.entries)
    restored: list[QueueEntry] = []
    for group in undo.groups:
        present = {entry.id for entry in entries}
        candidates = [entry for entry in group.entries if entry.id not in present]
        if not candidates:
            continue
        insert_at = len(entries)
        if group.next_id is not None:
            insert_at = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry.id == group.next_id
                ),
                insert_at,
            )
        elif group.previous_id is not None:
            previous = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry.id == group.previous_id
                ),
                None,
            )
            if previous is not None:
                insert_at = previous + 1
        entries[insert_at:insert_at] = candidates
        restored.extend(candidates)

    queue = _queue(state.queue, tuple(entries))
    outcome = MutationOutcome(
        PlayerAction.QUEUE_RESTORED,
        restored_count=len(restored),
        entry_ids=tuple(entry.id for entry in restored),
    )
    return Transition(
        replace(state, queue=queue),
        outcome,
        consume_undo=undo.id,
    )


def _start_radio(
    state: PlayerState,
    command: StartRadio,
    actor_id: UserId,
    now: datetime,
) -> Transition:
    if state.radio is not None and state.radio.active:
        if (
            command.expected_generation is None
            or command.expected_generation != state.radio.generation
        ):
            raise PlayerError(PlayerErrorCode.RADIO_CONFLICT, 409)
    generation = uuid4()
    request_id = uuid4()
    run = RadioRun(
        RadioRunId(uuid4()),
        state.session.id,
        command.seed,
        actor_id,
        now,
        generation,
        state=RadioState.LOADING,
        request_id=request_id,
    )
    event = RadioRefillRequested(
        state.session.id,
        run.id,
        generation,
        request_id,
        run.seed,
        None,
    )
    return Transition(
        replace(state, radio=run),
        MutationOutcome(PlayerAction.RADIO_STARTED),
        (event,),
    )


def _stop_radio(
    state: PlayerState,
    command: StopRadio,
    now: datetime,
) -> Transition:
    run = _radio(state, command.expected_generation)
    stopped = replace(
        run,
        state=RadioState.WAITING,
        candidates=(),
        request_id=None,
        ended_at=now,
    )
    return Transition(
        replace(state, radio=stopped),
        MutationOutcome(PlayerAction.RADIO_STOPPED),
    )


def _retry_radio(
    state: PlayerState,
    command: RetryRadio,
    now: datetime,
) -> Transition:
    run = _radio(state, command.expected_generation)
    if run.state is RadioState.LOADING:
        raise PlayerError(PlayerErrorCode.RADIO_CONFLICT, 409)
    request_id = uuid4()
    loading = replace(run, state=RadioState.LOADING, request_id=request_id, error=None)
    event = RadioRefillRequested(
        state.session.id,
        run.id,
        run.generation,
        request_id,
        run.seed,
        run.continuation,
    )
    return Transition(
        replace(state, radio=loading),
        MutationOutcome(PlayerAction.RADIO_RETRIED),
        (event,),
    )


def _apply_radio(
    state: PlayerState,
    command: ApplyRadioCandidates,
    now: datetime,
) -> Transition:
    run = state.radio
    if (
        run is None
        or not run.active
        or run.id != command.run_id
        or run.generation != command.generation
        or run.request_id != command.request_id
    ):
        raise PlayerError(PlayerErrorCode.RADIO_CONFLICT, 409)
    if command.error is not None:
        waiting = replace(
            run,
            state=RadioState.WAITING,
            request_id=None,
            error=command.error[:500],
        )
        return Transition(
            replace(state, radio=waiting),
            MutationOutcome(PlayerAction.RADIO_FAILED),
        )

    known = set(run.excluded_track_ids)
    known.update(entry.track_id for entry in state.queue.entries)
    candidates = list(run.candidates)
    skipped = 0
    accepted = 0
    for selection in command.selections:
        if selection.source_id is None or selection.track_id in known:
            skipped += 1
            continue
        candidates.append(
            RadioCandidate(
                RadioCandidateId(uuid4()),
                run.id,
                selection.track_id,
                selection.source_id,
                len(candidates),
            )
        )
        known.add(selection.track_id)
        accepted += 1

    if accepted == 0 and command.continuation is None:
        waiting = replace(
            run,
            state=RadioState.WAITING,
            request_id=None,
            error=None,
        )
        return Transition(
            replace(state, radio=waiting),
            MutationOutcome(PlayerAction.RADIO_FILLED, skipped_count=skipped),
        )

    ready = replace(
        run,
        state=RadioState.ACTIVE,
        request_id=None,
        candidates=tuple(candidates),
        continuation=command.continuation,
        error=None,
    )
    updated, events = _maintain_radio(replace(state, radio=ready), now)
    added = len(updated.queue.entries) - len(state.queue.entries)
    outcome = MutationOutcome(
        PlayerAction.RADIO_FILLED,
        added_count=added,
        skipped_count=skipped,
    )
    return Transition(updated, outcome, events)


def _maintain_radio(
    state: PlayerState,
    now: datetime,
) -> tuple[PlayerState, tuple[PlayerEvent, ...]]:
    run = state.radio
    if run is None or not run.active:
        return state, ()

    radio_count = sum(
        entry.request.origin is RequestOrigin.RADIO for entry in state.queue.entries
    )
    required = max(0, RADIO_TARGET - radio_count)
    chosen = run.candidates[:required]
    entries = list(state.queue.entries)
    excluded = set(run.excluded_track_ids)
    for candidate in chosen:
        request = new_request(
            state.session.id,
            candidate.track_id,
            candidate.source_id,
            now,
            radio_run_id=run.id,
        )
        entries.append(
            QueueEntry(
                QueueEntryId(uuid4()),
                state.session.id,
                request,
                len(entries),
            )
        )
        excluded.add(candidate.track_id)

    remaining = tuple(
        replace(candidate, position=index)
        for index, candidate in enumerate(run.candidates[len(chosen) :])
    )
    updated_run = replace(
        run,
        candidates=remaining,
        excluded_track_ids=frozenset(excluded),
    )
    updated = replace(
        state, queue=_queue(state.queue, tuple(entries)), radio=updated_run
    )
    radio_count += len(chosen)
    if radio_count >= RADIO_TARGET or updated_run.state is RadioState.LOADING:
        return updated, ()

    request_id = uuid4()
    loading = replace(
        updated_run,
        state=RadioState.LOADING,
        request_id=request_id,
        error=None,
    )
    event = RadioRefillRequested(
        state.session.id,
        loading.id,
        loading.generation,
        request_id,
        loading.seed,
        loading.continuation,
    )
    return replace(updated, radio=loading), (event,)


def _queue(queue: Queue, entries: tuple[QueueEntry, ...]) -> Queue:
    return replace(
        queue,
        entries=tuple(
            replace(entry, position=index) for index, entry in enumerate(entries)
        ),
    )


def _queue_revision(state: PlayerState, expected: int) -> None:
    if expected != state.session.queue_revision:
        raise PlayerError(PlayerErrorCode.QUEUE_CONFLICT, 409)


def _entry_index(queue: Queue, entry_id: QueueEntryId) -> int:
    try:
        return next(
            index for index, entry in enumerate(queue.entries) if entry.id == entry_id
        )
    except StopIteration as error:
        raise PlayerError(PlayerErrorCode.QUEUE_ENTRY_NOT_FOUND, 404) from error


def _radio(state: PlayerState, generation: UUID) -> RadioRun:
    run = state.radio
    if run is None or not run.active:
        raise PlayerError(PlayerErrorCode.RADIO_NOT_ACTIVE, 409)
    if run.generation != generation:
        raise PlayerError(PlayerErrorCode.RADIO_CONFLICT, 409)
    return run


def _undo_record(
    session_id: ListeningSessionId,
    actor_id: UserId,
    now: datetime,
    groups: tuple[UndoGroup, ...],
) -> QueueUndo:
    return QueueUndo(
        UndoId(uuid4()),
        session_id,
        actor_id,
        now,
        now + UNDO_LIFETIME,
        groups,
    )


def _id_before(queue: Queue, index: int) -> QueueEntryId | None:
    return queue.entries[index - 1].id if index > 0 else None


def _id_after(queue: Queue, index: int) -> QueueEntryId | None:
    return queue.entries[index + 1].id if index + 1 < len(queue.entries) else None


def _groups(
    queue: Queue,
    selected_ids: set[QueueEntryId],
) -> tuple[UndoGroup, ...]:
    groups: list[UndoGroup] = []
    index = 0
    while index < len(queue.entries):
        if queue.entries[index].id not in selected_ids:
            index += 1
            continue
        start = index
        while index < len(queue.entries) and queue.entries[index].id in selected_ids:
            index += 1
        groups.append(
            UndoGroup(
                queue.entries[start:index],
                _id_before(queue, start),
                _id_after(queue, index - 1),
            )
        )
    return tuple(groups)

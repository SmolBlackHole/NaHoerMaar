# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Playback policy. Decisions contain values and effects, never I/O or tasks."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from math import isfinite
from uuid import UUID, uuid4

from ..audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioProgress,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    VoiceConnection,
    VoiceDisconnected,
)
from .queue import Queue, QueueEntry
from .radio import ManualStrategy, RadioStrategy
from .sessions import (
    PlaybackCheckpoint,
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackPhase,
    PlaybackRecord,
    Preparation,
    SessionSnapshot,
)


class Control(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    SKIP = "skip"
    STOP = "stop"
    LEAVE = "leave"
    RESTORE = "restore"
    CHECKPOINT = "checkpoint"
    QUIESCE = "quiesce"
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class Seek:
    seconds: float


@dataclass(frozen=True, slots=True)
class Join:
    channel_id: int


@dataclass(frozen=True, slots=True)
class SetVolume:
    volume: float


@dataclass(frozen=True, slots=True)
class SetCrossfade:
    seconds: int


type PlaybackCommand = Control | Seek | Join | SetVolume | SetCrossfade


@dataclass(frozen=True, slots=True)
class SourceResolved:
    attempt_id: UUID
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class AttemptFailed:
    attempt_id: UUID


@dataclass(frozen=True, slots=True)
class Joined:
    connection: VoiceConnection


@dataclass(frozen=True, slots=True)
class JoinFailed:
    connection_id: UUID


@dataclass(frozen=True, slots=True)
class Prepared:
    preparation_id: UUID
    accepted: bool


@dataclass(frozen=True, slots=True)
class TransitionFailed:
    attempt_id: UUID
    preparation_id: UUID


type PlaybackMessage = (
    PlaybackCommand
    | SourceResolved
    | AttemptFailed
    | Joined
    | JoinFailed
    | Prepared
    | TransitionFailed
    | AudioEvent
    | VoiceDisconnected
)


@dataclass(frozen=True, slots=True)
class StartAttempt:
    attempt_id: UUID
    track_id: UUID


@dataclass(frozen=True, slots=True)
class StopOutput:
    attempt_id: UUID


@dataclass(frozen=True, slots=True)
class ChangePause:
    attempt_id: UUID
    paused: bool


@dataclass(frozen=True, slots=True)
class Connect:
    channel_id: int
    connection_id: UUID


@dataclass(frozen=True, slots=True)
class Disconnect:
    connection_id: UUID


@dataclass(frozen=True, slots=True)
class PrepareNext:
    outgoing_attempt_id: UUID
    preparation_id: UUID
    track_id: UUID
    seconds: float


@dataclass(frozen=True, slots=True)
class DiscardNext:
    preparation_id: UUID


@dataclass(frozen=True, slots=True)
class Activate:
    outgoing_attempt_id: UUID
    preparation_id: UUID
    attempt_id: UUID


type Effect = (
    StartAttempt
    | StopOutput
    | ChangePause
    | Connect
    | Disconnect
    | PrepareNext
    | DiscardNext
    | Activate
    | SetVolume
)


@dataclass(frozen=True, slots=True)
class Decision:
    snapshot: SessionSnapshot
    effects: tuple[Effect, ...] = ()
    code: str = "ok"


def decide(
    snapshot: SessionSnapshot,
    message: PlaybackMessage,
    *,
    now: datetime,
    progress: AudioProgress | None = None,
    connection: VoiceConnection | None = None,
) -> Decision:
    """One transition against committed state and an atomic transport observation.

    Connection loss wins over EOF/retry. Logical play identity belongs to the
    checkpoint; a new technical attempt never creates another listen by itself.
    """
    unchanged = Decision(snapshot)
    state, checkpoint = snapshot.playback, snapshot.checkpoint
    effects: list[Effect] = []

    def finish(play_id: UUID | None, reason: PlaybackEndReason) -> None:
        nonlocal snapshot
        snapshot = replace(
            snapshot,
            history=tuple(
                replace(record, ended_at=max(now, record.started_at), end_reason=reason)
                if record.id == play_id and record.ended_at is None
                else record
                for record in snapshot.history
            ),
        )

    def discard() -> None:
        nonlocal state
        if state.preparation:
            effects.append(DiscardNext(state.preparation.id))
            state = replace(state, preparation=None)

    def stop_output() -> None:
        nonlocal state
        discard()
        if state.attempt_id:
            effects.append(StopOutput(state.attempt_id))
        finish(state.outgoing_play_id, PlaybackEndReason.COMPLETED)
        state = replace(
            state, attempt_id=None, outgoing_play_id=None, transition_id=None
        )

    def load(*, retry: bool = False) -> None:
        nonlocal state
        stop_output()
        if checkpoint.track_id is None:
            return
        identifier = uuid4()
        state = replace(
            state,
            attempt_id=identifier,
            phase=PlaybackPhase.RESOLVING,
            retries=state.retries + 1 if retry else 0,
            waiting_for_queue=False,
            failed_preparation=None,
            error=None,
        )
        effects.append(StartAttempt(identifier, checkpoint.track_id))

    def advance(reason: PlaybackEndReason, *, paused: bool = False) -> None:
        nonlocal snapshot, checkpoint, state
        finish(checkpoint.play_id, reason)
        stop_output()
        checkpoint = PlaybackCheckpoint(snapshot.settings.id)
        state = replace(
            state, phase=PlaybackPhase.IDLE, duration_seconds=None, retries=0
        )
        if snapshot.queue.entries:
            entry, *remaining = snapshot.queue.entries
            snapshot = replace(
                snapshot, queue=Queue(snapshot.settings.id, tuple(remaining))
            )
            checkpoint = PlaybackCheckpoint(
                snapshot.settings.id,
                PlaybackIntent.PAUSED if paused else PlaybackIntent.PLAYING,
                entry.id,
                entry.track_id,
                added_by=entry.added_by,
                origin=entry.origin,
            )
            load()
        else:
            state = replace(
                state,
                waiting_for_queue=not paused
                and isinstance(snapshot.strategy, RadioStrategy),
            )

    # Reject old facts before copying their progress or changing any lifecycle.
    if isinstance(
        message, (AudioStarted, AudioCompleted, SourceResolved, AttemptFailed)
    ):
        if message.attempt_id != state.attempt_id:
            return unchanged
    if isinstance(message, (AudioStarted, AudioCompleted)):
        checkpoint = replace(checkpoint, position_seconds=message.position_seconds)
    elif progress is not None and progress.attempt_id == state.attempt_id:
        checkpoint = replace(checkpoint, position_seconds=progress.position_seconds)

    if isinstance(message, (AudioCompleted, AttemptFailed)) and (
        connection is None or connection.connection_id != state.connection_id
    ):
        stop_output()
        state = replace(state, connection_id=None, phase=PlaybackPhase.SUSPENDED)
    elif isinstance(message, VoiceDisconnected):
        if message.connection.connection_id != state.connection_id:
            return unchanged
        if message.progress and message.progress.attempt_id == state.attempt_id:
            checkpoint = replace(
                checkpoint, position_seconds=message.progress.position_seconds
            )
        stop_output()
        state = replace(
            state,
            connection_id=None,
            phase=PlaybackPhase.SUSPENDED
            if checkpoint.track_id
            else PlaybackPhase.IDLE,
        )
    elif isinstance(message, Join) or message is Control.RESTORE:
        channel = (
            message.channel_id
            if isinstance(message, Join)
            else snapshot.settings.channel_id
        )
        if channel is None:
            return unchanged
        if type(channel) is not int or channel <= 0:
            raise ValueError("Invalid channel.")
        if (
            connection
            and connection.connection_id == state.connection_id
            and connection.channel_id == channel
        ):
            if isinstance(message, Join) and state.attempt_id is None:
                if checkpoint.track_id:
                    load()
                else:
                    advance(PlaybackEndReason.COMPLETED)
        else:
            stop_output()
            previous_connection = state.connection_id or state.joining_id
            if previous_connection:
                effects.append(Disconnect(previous_connection))
            joining_id = uuid4()
            snapshot = replace(
                snapshot, settings=replace(snapshot.settings, channel_id=channel)
            )
            state = replace(
                state,
                connection_id=None,
                joining_id=joining_id,
                start_queue_on_join=isinstance(message, Join),
                phase=PlaybackPhase.SUSPENDED
                if checkpoint.track_id
                else PlaybackPhase.IDLE,
            )
            effects.append(Connect(channel, joining_id))
    elif isinstance(message, Joined):
        if message.connection.connection_id != state.joining_id:
            return unchanged
        start_queue = state.start_queue_on_join
        state = replace(
            state,
            connection_id=state.joining_id,
            joining_id=None,
            start_queue_on_join=False,
            error=None,
        )
        if checkpoint.track_id:
            load()
        elif start_queue:
            advance(PlaybackEndReason.COMPLETED)
    elif isinstance(message, JoinFailed):
        if message.connection_id != state.joining_id:
            return unchanged
        state = replace(
            state, joining_id=None, start_queue_on_join=False, error="connection_failed"
        )
    elif message in (Control.LEAVE, Control.QUIESCE):
        stop_output()
        target = state.connection_id or state.joining_id
        if target:
            effects.append(Disconnect(target))
        state = replace(
            state,
            connection_id=None,
            joining_id=None,
            start_queue_on_join=False,
            waiting_for_queue=False,
            phase=PlaybackPhase.SUSPENDED
            if checkpoint.track_id
            else PlaybackPhase.IDLE,
        )
        if message is Control.LEAVE:
            snapshot = replace(
                snapshot,
                strategy=ManualStrategy(),
                settings=replace(snapshot.settings, channel_id=None),
            )
    elif isinstance(message, SetVolume):
        snapshot = replace(
            snapshot, settings=replace(snapshot.settings, volume=message.volume)
        )
        effects.append(message)
    elif isinstance(message, SetCrossfade):
        snapshot = replace(
            snapshot,
            settings=replace(snapshot.settings, crossfade_seconds=message.seconds),
        )
        discard()
        state = replace(state, failed_preparation=None)
    elif message is Control.STOP:
        stop_output()
        finish(checkpoint.play_id, PlaybackEndReason.STOPPED)
        if checkpoint.entry_id and checkpoint.track_id:
            entry = QueueEntry(
                snapshot.settings.id,
                checkpoint.track_id,
                0,
                checkpoint.added_by,
                checkpoint.origin,
                checkpoint.entry_id,
            )
            snapshot = replace(
                snapshot,
                queue=Queue(snapshot.settings.id, (entry, *snapshot.queue.entries)),
            )
        checkpoint = PlaybackCheckpoint(snapshot.settings.id)
        snapshot = replace(snapshot, strategy=ManualStrategy())
        state = replace(
            state,
            phase=PlaybackPhase.IDLE,
            waiting_for_queue=False,
            duration_seconds=None,
            error=None,
        )
    elif message is Control.PAUSE:
        if checkpoint.track_id is None:
            return Decision(snapshot, code="nothing_playing")
        checkpoint = replace(checkpoint, intent=PlaybackIntent.PAUSED)
        if state.attempt_id:
            effects.append(ChangePause(state.attempt_id, True))
            if state.phase is PlaybackPhase.PLAYING:
                state = replace(state, phase=PlaybackPhase.PAUSED)
    elif (
        message is Control.PLAY or isinstance(message, Seek) or message is Control.SKIP
    ):
        if connection is None or connection.connection_id != state.connection_id:
            return Decision(snapshot, code="not_connected")
        if isinstance(message, Seek):
            if (
                checkpoint.track_id is None
                or state.duration_seconds is None
                or not isfinite(message.seconds)
                or not 0 <= message.seconds < state.duration_seconds
            ):
                return Decision(snapshot, code="invalid_seek")
            checkpoint = replace(checkpoint, position_seconds=message.seconds)
            load()
        elif message is Control.SKIP:
            advance(PlaybackEndReason.SKIPPED)
        elif checkpoint.track_id is None:
            advance(PlaybackEndReason.COMPLETED)
        else:
            checkpoint = replace(checkpoint, intent=PlaybackIntent.PLAYING)
            if state.attempt_id is None:
                load()
            else:
                effects.append(ChangePause(state.attempt_id, False))
                if state.phase is PlaybackPhase.PAUSED:
                    state = replace(state, phase=PlaybackPhase.PLAYING)
    elif isinstance(message, SourceResolved):
        if state.phase is not PlaybackPhase.RESOLVING:
            return unchanged
        state = replace(
            state,
            phase=PlaybackPhase.STARTING,
            duration_seconds=message.duration_seconds,
        )
    elif isinstance(message, AudioStarted):
        if state.phase is not PlaybackPhase.STARTING:
            return unchanged
        if checkpoint.play_id is None:
            if checkpoint.track_id is None or checkpoint.entry_id is None:
                raise ValueError("Output confirmation needs a current entry.")
            record = PlaybackRecord(
                snapshot.settings.id,
                checkpoint.track_id,
                checkpoint.entry_id,
                now,
                checkpoint.added_by,
                checkpoint.origin,
            )
            snapshot = replace(snapshot, history=(record, *snapshot.history))
            checkpoint = replace(checkpoint, play_id=record.id)
        state = replace(
            state,
            phase=PlaybackPhase.PAUSED
            if checkpoint.intent is PlaybackIntent.PAUSED
            else PlaybackPhase.PLAYING,
        )
    elif isinstance(message, (AudioCompleted, AttemptFailed)):
        if (
            isinstance(message, AudioCompleted)
            and message.reason is AudioEndReason.INTERRUPTED
            and state.retries < 1
        ):
            load(retry=True)
        elif (
            isinstance(message, AudioCompleted)
            and message.reason is AudioEndReason.STOPPED
        ):
            # An unexpected output stop retains the current occurrence for rejoin.
            stop_output()
            state = replace(state, phase=PlaybackPhase.SUSPENDED)
        else:
            failed = (
                not isinstance(message, AudioCompleted)
                or message.reason is not AudioEndReason.NATURAL
                or checkpoint.play_id is None
            )
            advance(
                PlaybackEndReason.FAILED if failed else PlaybackEndReason.COMPLETED,
                paused=checkpoint.intent is PlaybackIntent.PAUSED,
            )
            if failed:
                state = replace(state, error="track_failed")
    elif isinstance(message, Prepared):
        if state.preparation is None or state.preparation.id != message.preparation_id:
            return unchanged
        state = (
            replace(state, preparation=replace(state.preparation, ready=True))
            if message.accepted
            else replace(
                state, failed_preparation=state.preparation.entry_id, preparation=None
            )
        )
    elif isinstance(message, CrossfadeDue):
        preparation = state.preparation
        if (
            message.outgoing_attempt_id != state.attempt_id
            or preparation is None
            or message.preparation_id != preparation.id
            or not snapshot.queue.entries
            or snapshot.queue.entries[0].id != preparation.entry_id
        ):
            return unchanged
        if checkpoint.intent is PlaybackIntent.PAUSED:
            discard()
            state = replace(state, failed_preparation=preparation.entry_id)
            return Decision(
                replace(snapshot, checkpoint=checkpoint, playback=state), tuple(effects)
            )
        entry, *remaining = snapshot.queue.entries
        snapshot = replace(
            snapshot, queue=Queue(snapshot.settings.id, tuple(remaining))
        )
        outgoing_play_id = checkpoint.play_id
        checkpoint = PlaybackCheckpoint(
            snapshot.settings.id,
            checkpoint.intent,
            entry.id,
            entry.track_id,
            added_by=entry.added_by,
            origin=entry.origin,
        )
        attempt_id = uuid4()
        effects.append(
            Activate(message.outgoing_attempt_id, preparation.id, attempt_id)
        )
        state = replace(
            state,
            attempt_id=attempt_id,
            phase=PlaybackPhase.STARTING,
            preparation=None,
            outgoing_play_id=outgoing_play_id,
            transition_id=preparation.id,
            duration_seconds=message.duration_seconds,
            retries=0,
            failed_preparation=None,
        )
    elif isinstance(message, CrossfadeCompleted):
        if (
            message.attempt_id != state.attempt_id
            or message.preparation_id != state.transition_id
        ):
            return unchanged
        finish(state.outgoing_play_id, PlaybackEndReason.COMPLETED)
        state = replace(state, outgoing_play_id=None, transition_id=None)
    elif isinstance(message, TransitionFailed):
        if (
            message.attempt_id != state.attempt_id
            or message.preparation_id != state.transition_id
        ):
            return unchanged
        load()
    elif message is Control.RECONCILE:
        if state.waiting_for_queue and not isinstance(snapshot.strategy, RadioStrategy):
            state = replace(state, waiting_for_queue=False)
        if state.waiting_for_queue and snapshot.queue.entries and state.connection_id:
            advance(PlaybackEndReason.COMPLETED)

    # Prepare only the actual head, once per attempt/head/settings combination.
    head = snapshot.queue.entries[0] if snapshot.queue.entries else None
    if state.preparation and (head is None or head.id != state.preparation.entry_id):
        discard()
    if (
        state.phase in (PlaybackPhase.PLAYING, PlaybackPhase.PAUSED)
        and state.attempt_id
        and state.transition_id is None
        and state.preparation is None
        and head
        and head.id != state.failed_preparation
        and state.duration_seconds
        and snapshot.settings.crossfade_seconds
    ):
        preparation = Preparation(head.id)
        effects.append(
            PrepareNext(
                state.attempt_id,
                preparation.id,
                head.track_id,
                min(snapshot.settings.crossfade_seconds, state.duration_seconds / 2),
            )
        )
        state = replace(state, preparation=preparation)
    return Decision(
        replace(snapshot, checkpoint=checkpoint, playback=state), tuple(effects)
    )

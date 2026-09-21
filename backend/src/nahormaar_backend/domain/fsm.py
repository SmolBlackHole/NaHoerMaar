# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Pure playback decisions; the controller commits and executes their effects."""

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from .checkpoint import PlaybackCheckpoint
from .models import PlaybackState, PlayerSnapshot, VoiceState


class LifecycleEvent(StrEnum):
    RECOVER = "recover"
    CONNECTING = "connecting"
    JOINED = "joined"
    RESTORED = "restored"
    DISCONNECTED = "disconnected"
    LEAVE = "leave"
    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    SKIP = "skip"
    STOP = "stop"
    STARTED = "started"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    STOPPED = "stopped"
    CROSSFADE = "crossfade"
    FAULT = "fault"


class InvalidTransitionError(ValueError):
    def __init__(self, state: PlaybackState, event: LifecycleEvent) -> None:
        self.state = state
        self.event = event
        super().__init__(f"Cannot apply {event.value!r} while {state.value!r}.")


class PlaybackEffect(StrEnum):
    STOP_OUTPUT = "stop_output"
    LOAD = "load"
    RESUME_OUTPUT = "resume_output"
    PAUSE_OUTPUT = "pause_output"
    RECORD_HISTORY = "record_history"
    REPORT_FAILURE = "report_failure"


@dataclass(frozen=True, slots=True)
class PlaybackContext:
    """One logical play, its current output attempt and retained resume intent."""

    entry_id: UUID | None = None
    attempt_id: UUID | None = None
    position_seconds: float = 0
    paused: bool = False
    retries: int = 0
    started: bool = False
    history_recorded: bool = False
    suspended: bool = False
    channel_id: int | None = None
    rejoin: bool = False


@dataclass(frozen=True, slots=True)
class PlaybackDecision:
    snapshot: PlayerSnapshot
    context: PlaybackContext
    effects: tuple[PlaybackEffect, ...] = ()


def decide_playback(
    snapshot: PlayerSnapshot,
    context: PlaybackContext,
    event: LifecycleEvent,
    *,
    attempt_id: UUID | None = None,
    position_seconds: float | None = None,
    channel_id: int | None = None,
    checkpoint: PlaybackCheckpoint | None = None,
) -> PlaybackDecision:
    """Decide lifecycle policy without clocks, storage, tasks or adapter calls."""
    unchanged = PlaybackDecision(snapshot, context)
    if event in {
        LifecycleEvent.STARTED,
        LifecycleEvent.FINISHED,
        LifecycleEvent.INTERRUPTED,
        LifecycleEvent.FAILED,
        LifecycleEvent.STOPPED,
    } and (attempt_id is None or attempt_id != context.attempt_id):
        return unchanged
    if position_seconds is not None:
        context = replace(context, position_seconds=max(0, position_seconds))

    def load(current: PlayerSnapshot, intent: PlaybackContext) -> PlaybackDecision:
        if current.current is None:
            return PlaybackDecision(
                current,
                PlaybackContext(
                    channel_id=intent.channel_id,
                    rejoin=intent.rejoin,
                ),
                (PlaybackEffect.STOP_OUTPUT,),
            )
        return PlaybackDecision(
            replace(
                current,
                state=PlaybackState.PAUSED if intent.paused else PlaybackState.LOADING,
            ),
            replace(
                intent,
                entry_id=current.current.id,
                attempt_id=None,
                started=False,
                suspended=False,
            ),
            (PlaybackEffect.STOP_OUTPUT, PlaybackEffect.LOAD),
        )

    def advance(*, report: bool = False) -> PlaybackDecision:
        current = snapshot.upcoming[0] if snapshot.upcoming else None
        result = load(
            replace(
                snapshot,
                current=current,
                upcoming=snapshot.upcoming[1:],
                state=PlaybackState.LOADING if current else PlaybackState.IDLE,
            ),
            PlaybackContext(
                channel_id=context.channel_id,
                rejoin=context.rejoin,
            ),
        )
        return (
            replace(result, effects=(PlaybackEffect.REPORT_FAILURE, *result.effects))
            if report
            else result
        )

    if event is LifecycleEvent.RECOVER:
        if checkpoint is not None:
            return PlaybackDecision(
                replace(
                    snapshot,
                    state=PlaybackState.LOADING
                    if snapshot.current
                    else PlaybackState.IDLE,
                    voice_state=VoiceState.DISCONNECTED,
                ),
                PlaybackContext(
                    entry_id=checkpoint.entry_id,
                    position_seconds=checkpoint.position_seconds,
                    paused=checkpoint.paused,
                    history_recorded=checkpoint.history_recorded,
                    suspended=checkpoint.entry_id is not None,
                    channel_id=checkpoint.channel_id,
                    rejoin=True,
                ),
            )
        return PlaybackDecision(
            replace(
                snapshot,
                state=PlaybackState.IDLE,
                current=None,
                upcoming=((snapshot.current,) if snapshot.current else ())
                + snapshot.upcoming,
                voice_state=VoiceState.DISCONNECTED,
            ),
            PlaybackContext(),
        )
    if event is LifecycleEvent.CONNECTING:
        return PlaybackDecision(
            replace(snapshot, voice_state=VoiceState.CONNECTING), context
        )
    if event is LifecycleEvent.FAULT:
        return PlaybackDecision(
            replace(snapshot, state=PlaybackState.ERROR)
            if snapshot.current
            else snapshot,
            replace(context, attempt_id=None, rejoin=False),
        )
    if event is LifecycleEvent.STARTED:
        if context.started or snapshot.current is None:
            return unchanged
        return PlaybackDecision(
            replace(
                snapshot,
                state=PlaybackState.PAUSED if context.paused else PlaybackState.PLAYING,
            ),
            replace(context, started=True, history_recorded=True),
            () if context.history_recorded else (PlaybackEffect.RECORD_HISTORY,),
        )
    if event is LifecycleEvent.FINISHED:
        return advance(report=not context.started)
    if event in (LifecycleEvent.INTERRUPTED, LifecycleEvent.FAILED):
        if event is LifecycleEvent.INTERRUPTED and context.retries < 1:
            return load(snapshot, replace(context, retries=context.retries + 1))
        return advance(report=True)
    if event in (LifecycleEvent.LEAVE, LifecycleEvent.DISCONNECTED):
        paused = context.paused or snapshot.state is PlaybackState.PAUSED
        return PlaybackDecision(
            replace(
                snapshot,
                voice_state=VoiceState.DISCONNECTED,
                state=(PlaybackState.PAUSED if paused else PlaybackState.LOADING)
                if snapshot.current
                else PlaybackState.IDLE,
            ),
            replace(
                context,
                attempt_id=None,
                started=False,
                suspended=bool(snapshot.current),
                paused=paused,
                rejoin=event is LifecycleEvent.DISCONNECTED and context.rejoin,
            ),
            (PlaybackEffect.STOP_OUTPUT,),
        )
    if event in (LifecycleEvent.JOINED, LifecycleEvent.RESTORED):
        context = replace(context, channel_id=channel_id, rejoin=True)
        snapshot = replace(snapshot, voice_state=VoiceState.CONNECTED)
        if context.attempt_id is not None and not context.suspended:
            return PlaybackDecision(snapshot, context)
        if snapshot.current is None:
            if event is LifecycleEvent.RESTORED:
                return PlaybackDecision(snapshot, context)
            return advance()
        return load(snapshot, context)
    if event is LifecycleEvent.PLAY:
        if context.suspended:
            return load(snapshot, context)
        if snapshot.state in (PlaybackState.PLAYING, PlaybackState.LOADING):
            return unchanged
        if snapshot.state is PlaybackState.PAUSED:
            return PlaybackDecision(
                replace(
                    snapshot,
                    state=PlaybackState.PLAYING
                    if context.started
                    else PlaybackState.LOADING,
                ),
                replace(context, paused=False),
                (PlaybackEffect.RESUME_OUTPUT,),
            )
        return advance() if snapshot.current is None else load(snapshot, context)
    if event is LifecycleEvent.PAUSE:
        if snapshot.state not in (PlaybackState.PLAYING, PlaybackState.LOADING):
            raise InvalidTransitionError(snapshot.state, event)
        return PlaybackDecision(
            replace(snapshot, state=PlaybackState.PAUSED),
            replace(context, paused=True),
            (PlaybackEffect.PAUSE_OUTPUT,),
        )
    if event is LifecycleEvent.SEEK:
        if snapshot.state not in (PlaybackState.PLAYING, PlaybackState.PAUSED):
            raise InvalidTransitionError(snapshot.state, event)
        return load(snapshot, context)
    if event is LifecycleEvent.SKIP:
        return advance() if snapshot.current else load(snapshot, context)
    if event in (LifecycleEvent.STOP, LifecycleEvent.STOPPED):
        return PlaybackDecision(
            replace(
                snapshot,
                state=PlaybackState.IDLE,
                current=None,
                upcoming=((snapshot.current,) if snapshot.current else ())
                + snapshot.upcoming,
            ),
            PlaybackContext(
                channel_id=context.channel_id,
                rejoin=context.rejoin,
            ),
            (PlaybackEffect.STOP_OUTPUT,),
        )
    if event is LifecycleEvent.CROSSFADE:
        if snapshot.state is not PlaybackState.PLAYING or not snapshot.upcoming:
            raise InvalidTransitionError(snapshot.state, event)
        # Keep outgoing output until the adapter activates the reservation.
        return replace(advance(), effects=())
    raise AssertionError(f"Unhandled playback event: {event}")

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Pure, deterministic playback transitions. Unlisted transitions are invalid."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum, StrEnum, auto
from types import MappingProxyType

from .models import PlaybackState, PlayerSnapshot, VoiceState


class PlaybackEvent(StrEnum):
    PLAY = "play"
    READY = "ready"
    PAUSE = "pause"
    SKIP = "skip"
    STOP = "stop"
    FAIL = "fail"
    RECOVER = "recover"


class InvalidTransitionError(ValueError):
    def __init__(self, state: PlaybackState, event: PlaybackEvent) -> None:
        self.state = state
        self.event = event
        super().__init__(f"Cannot apply {event.value!r} while {state.value!r}.")


class _QueueEffect(Enum):
    KEEP = auto()
    ADVANCE = auto()
    REQUEUE = auto()


@dataclass(frozen=True, slots=True)
class _Transition:
    state: PlaybackState
    effect: _QueueEffect = _QueueEffect.KEEP


_TRANSITIONS: Mapping[PlaybackState, Mapping[PlaybackEvent, _Transition]] = (
    MappingProxyType(
        {
            PlaybackState.IDLE: MappingProxyType(
                {
                    PlaybackEvent.PLAY: _Transition(
                        PlaybackState.LOADING, _QueueEffect.ADVANCE
                    ),
                    PlaybackEvent.SKIP: _Transition(PlaybackState.IDLE),
                    PlaybackEvent.STOP: _Transition(PlaybackState.IDLE),
                    PlaybackEvent.RECOVER: _Transition(PlaybackState.IDLE),
                }
            ),
            PlaybackState.LOADING: MappingProxyType(
                {
                    PlaybackEvent.READY: _Transition(PlaybackState.PLAYING),
                    PlaybackEvent.SKIP: _Transition(
                        PlaybackState.LOADING, _QueueEffect.ADVANCE
                    ),
                    PlaybackEvent.STOP: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                    PlaybackEvent.FAIL: _Transition(PlaybackState.ERROR),
                    PlaybackEvent.RECOVER: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                }
            ),
            PlaybackState.PLAYING: MappingProxyType(
                {
                    PlaybackEvent.PAUSE: _Transition(PlaybackState.PAUSED),
                    PlaybackEvent.SKIP: _Transition(
                        PlaybackState.LOADING, _QueueEffect.ADVANCE
                    ),
                    PlaybackEvent.STOP: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                    PlaybackEvent.FAIL: _Transition(PlaybackState.ERROR),
                    PlaybackEvent.RECOVER: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                }
            ),
            PlaybackState.PAUSED: MappingProxyType(
                {
                    PlaybackEvent.PLAY: _Transition(PlaybackState.PLAYING),
                    PlaybackEvent.SKIP: _Transition(
                        PlaybackState.LOADING, _QueueEffect.ADVANCE
                    ),
                    PlaybackEvent.STOP: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                    PlaybackEvent.FAIL: _Transition(PlaybackState.ERROR),
                    PlaybackEvent.RECOVER: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                }
            ),
            PlaybackState.ERROR: MappingProxyType(
                {
                    PlaybackEvent.PLAY: _Transition(PlaybackState.LOADING),
                    PlaybackEvent.SKIP: _Transition(
                        PlaybackState.LOADING, _QueueEffect.ADVANCE
                    ),
                    PlaybackEvent.STOP: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                    PlaybackEvent.RECOVER: _Transition(
                        PlaybackState.IDLE, _QueueEffect.REQUEUE
                    ),
                }
            ),
        }
    )
)


def transition(snapshot: PlayerSnapshot, event: PlaybackEvent) -> PlayerSnapshot:
    """Return the next snapshot without mutating state or performing I/O."""
    rule = _TRANSITIONS[snapshot.state].get(event)
    if rule is None:
        raise InvalidTransitionError(snapshot.state, event)

    current, upcoming, state = snapshot.current, snapshot.upcoming, rule.state
    if rule.effect is _QueueEffect.ADVANCE:
        current = upcoming[0] if upcoming else None
        upcoming = upcoming[1:]
        if current is None:
            state = PlaybackState.IDLE
    elif rule.effect is _QueueEffect.REQUEUE:
        if current is not None:
            upcoming = (current, *upcoming)
        current = None

    return replace(
        snapshot,
        state=state,
        current=current,
        upcoming=upcoming,
        voice_state=(
            VoiceState.DISCONNECTED
            if event is PlaybackEvent.RECOVER
            else snapshot.voice_state
        ),
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Radio identity and explicit runtime state transitions."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

from .models import Contributor


@dataclass(frozen=True, slots=True)
class RadioSeed:
    kind: Literal["track", "playlist"]
    identifier: str
    title: str


class RadioState(StrEnum):
    OFF = "off"
    ACTIVE = "active"
    LOADING = "loading"
    WAITING = "waiting"


class RadioEvent(StrEnum):
    START = "start"
    FETCH = "fetch"
    READY = "ready"
    FAIL = "fail"
    RETRY = "retry"
    STOP = "stop"


def radio_transition(state: RadioState, event: RadioEvent) -> RadioState:
    if event is RadioEvent.STOP:
        return RadioState.OFF
    if event is RadioEvent.START:
        return RadioState.ACTIVE
    transitions = {
        (RadioState.ACTIVE, RadioEvent.FETCH): RadioState.LOADING,
        (RadioState.LOADING, RadioEvent.READY): RadioState.ACTIVE,
        (RadioState.LOADING, RadioEvent.FAIL): RadioState.WAITING,
        (RadioState.ACTIVE, RadioEvent.FAIL): RadioState.WAITING,
        (RadioState.WAITING, RadioEvent.RETRY): RadioState.ACTIVE,
    }
    try:
        return transitions[state, event]
    except KeyError:
        raise ValueError(f"Invalid radio transition: {state}, {event}") from None


@dataclass(frozen=True, slots=True)
class RadioStatus:
    state: RadioState = RadioState.OFF
    session_id: UUID | None = None
    seed: RadioSeed | None = None
    initiator: Contributor | None = None
    error: str | None = None
    event_id: UUID | None = None
    action: Literal["started", "stopped", "retried"] | None = None
    actor: Contributor | None = None

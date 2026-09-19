# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed control requests and durable outcomes, independent of HTTP."""

import json
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Add:
    source_url: str


@dataclass(frozen=True, slots=True)
class Remove:
    entry_id: UUID


@dataclass(frozen=True, slots=True)
class Move:
    entry_id: UUID
    before_entry_id: UUID | None
    expected_queue_revision: int


@dataclass(frozen=True, slots=True)
class Clear:
    expected_queue_revision: int


@dataclass(frozen=True, slots=True)
class Control:
    action: Literal["play", "pause", "skip", "stop"]
    expected_playback_id: UUID | None


@dataclass(frozen=True, slots=True)
class Volume:
    volume: float


@dataclass(frozen=True, slots=True)
class Connect:
    channel_id: int


@dataclass(frozen=True, slots=True)
class Disconnect:
    pass


type Command = Add | Remove | Move | Clear | Control | Volume | Connect | Disconnect


def fingerprint(command: Command) -> str:
    return json.dumps(
        [type(command).__name__, asdict(command)], sort_keys=True, default=str
    )


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str = "ok"
    status_code: int = 200
    entry_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Receipt:
    request_id: UUID
    fingerprint: str
    outcome: Outcome | None = None


@dataclass(frozen=True, slots=True)
class Revisions:
    revision: int = 0
    queue_revision: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.queue_revision <= self.revision:
            raise ValueError("Invalid player revisions.")

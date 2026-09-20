# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed control requests and durable outcomes, independent of HTTP."""

import json
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import UUID

from .models import ANONYMOUS_CONTRIBUTOR, Contributor


@dataclass(frozen=True, slots=True)
class Add:
    source_url: str
    added_by: Contributor | None = None


@dataclass(frozen=True, slots=True)
class AddMany:
    source_urls: tuple[str, ...]
    added_by: Contributor | None = None


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
    contributor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Control:
    action: Literal["play", "pause", "skip", "stop"]
    expected_playback_id: UUID | None


@dataclass(frozen=True, slots=True)
class Volume:
    volume: float


@dataclass(frozen=True, slots=True)
class Seek:
    position_seconds: float
    expected_playback_id: UUID


@dataclass(frozen=True, slots=True)
class Connect:
    channel_id: int


@dataclass(frozen=True, slots=True)
class Disconnect:
    pass


type Command = (
    Add
    | AddMany
    | Remove
    | Move
    | Clear
    | Control
    | Volume
    | Seek
    | Connect
    | Disconnect
)


def fingerprint(command: Command) -> str:
    payload = asdict(command)
    if isinstance(command, Clear) and command.contributor_id is None:
        # Existing whole-queue clear receipts keep their fingerprint.
        payload.pop("contributor_id")
    if isinstance(command, Add) and command.added_by in (None, ANONYMOUS_CONTRIBUTOR):
        # Keep receipts issued before queue attribution replayable.
        payload.pop("added_by")
    return json.dumps([type(command).__name__, payload], sort_keys=True, default=str)


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

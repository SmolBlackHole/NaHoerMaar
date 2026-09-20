# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed control requests and durable outcomes, independent of HTTP."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .models import ANONYMOUS_CONTRIBUTOR, Contributor, QueueEntry
from .undo import Removal


@dataclass(frozen=True, slots=True)
class Add:
    source_url: str
    added_by: Contributor | None = None


@dataclass(frozen=True, slots=True)
class AddMany:
    source_urls: tuple[str, ...]
    added_by: Contributor | None = None
    skip_duplicates: bool = False


@dataclass(frozen=True, slots=True)
class Remove:
    entry_id: UUID


@dataclass(frozen=True, slots=True)
class Undo:
    undo_id: UUID


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


@dataclass(frozen=True, slots=True)
class StartRadio:
    preview_id: UUID
    expected_session_id: UUID | None


@dataclass(frozen=True, slots=True)
class StopRadio:
    expected_session_id: UUID


@dataclass(frozen=True, slots=True)
class RetryRadio:
    expected_session_id: UUID


type Command = (
    Add
    | AddMany
    | Remove
    | Undo
    | Move
    | Clear
    | Control
    | Volume
    | Seek
    | Connect
    | Disconnect
    | StartRadio
    | StopRadio
    | RetryRadio
)


def fingerprint(command: Command, *, authenticated: bool = False) -> str:
    payload = asdict(command)
    if isinstance(command, AddMany) and not command.skip_duplicates:
        payload.pop("skip_duplicates")
    if authenticated and isinstance(command, (Add, AddMany)):
        # Attribution is supplied by the account, not the request. A profile edit
        # between a request and its retry must not change the request's identity.
        payload.pop("added_by")
    if isinstance(command, Clear) and command.contributor_id is None:
        # Existing whole-queue clear receipts keep their fingerprint.
        payload.pop("contributor_id")
    if (
        not authenticated
        and isinstance(command, Add)
        and command.added_by in (None, ANONYMOUS_CONTRIBUTOR)
    ):
        # Keep receipts issued before queue attribution replayable.
        payload.pop("added_by")
    return json.dumps([type(command).__name__, payload], sort_keys=True, default=str)


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str = "ok"
    status_code: int = 200
    entry_id: UUID | None = None
    added_count: int = 0
    skipped_count: int = 0
    removed_count: int = 0
    restored_count: int = 0
    undo_id: UUID | None = None
    undo_expires_at: datetime | None = None
    actor: Contributor | None = None
    entries: tuple[QueueEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class Receipt:
    request_id: UUID
    fingerprint: str
    outcome: Outcome | None = None
    actor_id: UUID | None = None
    removal: Removal | None = None
    consume_undo: UUID | None = None
    actor: Contributor | None = None


@dataclass(frozen=True, slots=True)
class Revisions:
    revision: int = 0
    queue_revision: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.queue_revision <= self.revision:
            raise ValueError("Invalid player revisions.")

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queue occurrences and attribution, without duplicated track metadata."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from re import fullmatch
from uuid import UUID, uuid4


class QueueOrigin(StrEnum):
    MANUAL = "manual"
    RADIO = "radio"


@dataclass(frozen=True, slots=True)
class Contributor:
    """Account identity and the display values captured when a track was added."""

    id: UUID
    name: str
    avatar: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= 32 or self.name != self.name.strip():
            raise ValueError(
                "A contributor needs a trimmed name of 1 to 32 characters."
            )
        if not fullmatch(r"[0-9a-f]{4}", self.avatar):
            raise ValueError("Invalid contributor avatar.")


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One occurrence of a track at a zero-based position within a session."""

    session_id: UUID
    track_id: UUID
    position: int
    added_by: Contributor | None = None
    origin: QueueOrigin = QueueOrigin.MANUAL
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 0:
            raise ValueError("Queue position must be a non-negative integer.")
        if type(self.origin) is not QueueOrigin:
            raise ValueError("Queue origin must be a QueueOrigin value.")


@dataclass(frozen=True, slots=True)
class Add:
    track_ids: tuple[UUID, ...]
    skip_duplicates: bool = False
    origin: QueueOrigin = QueueOrigin.MANUAL


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
class Undo:
    undo_id: UUID


type QueueCommand = Add | Remove | Move | Clear | Undo


class UndoUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RemovedGroup:
    entries: tuple[QueueEntry, ...]
    previous_id: UUID | None
    next_id: UUID | None


@dataclass(frozen=True, slots=True)
class Removal:
    id: UUID
    actor_id: UUID
    expires_at: datetime
    groups: tuple[RemovedGroup, ...]


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str = "ok"
    added_count: int = 0
    removed_count: int = 0
    restored_count: int = 0
    skipped_count: int = 0
    entries: tuple[QueueEntry, ...] = ()
    actor: Contributor | None = None
    undo_id: UUID | None = None
    undo_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Queue:
    """Immutable ordered occurrences; edits never perform I/O or publish facts."""

    session_id: UUID
    entries: tuple[QueueEntry, ...] = ()

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            entry.session_id != self.session_id for entry in self.entries
        ):
            raise ValueError("Queue entries must belong to this session.")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Queue entry IDs must be unique.")
        object.__setattr__(
            self,
            "entries",
            tuple(
                replace(entry, position=index)
                for index, entry in enumerate(self.entries)
            ),
        )

    def edit(
        self,
        command: QueueCommand,
        *,
        actor: Contributor | None,
        now: datetime,
        removal: Removal | None = None,
        current_track_id: UUID | None = None,
    ) -> tuple[Queue, Outcome, Removal | None]:
        entries = list(self.entries)
        outcome = Outcome(actor=actor)
        captured = None
        match command:
            case Add(track_ids, skip_duplicates, origin):
                if type(track_ids) is not tuple or not 1 <= len(track_ids) <= 100:
                    raise ValueError("A batch needs 1 to 100 tracks.")
                seen = {entry.track_id for entry in entries} | {current_track_id}
                added: list[QueueEntry] = []
                for track_id in track_ids:
                    if skip_duplicates and track_id in seen:
                        continue
                    seen.add(track_id)
                    added.append(
                        QueueEntry(self.session_id, track_id, 0, actor, origin)
                    )
                index = (
                    next(
                        (
                            i
                            for i, entry in enumerate(entries)
                            if entry.origin is QueueOrigin.RADIO
                        ),
                        len(entries),
                    )
                    if origin is QueueOrigin.MANUAL
                    else len(entries)
                )
                entries[index:index] = added
                outcome = replace(
                    outcome,
                    added_count=len(added),
                    skipped_count=len(track_ids) - len(added),
                    entries=tuple(added),
                )
            case Remove() | Clear():
                if isinstance(command, Remove):
                    if not any(entry.id == command.entry_id for entry in entries):
                        raise KeyError(command.entry_id)
                    removed = tuple(
                        entry for entry in entries if entry.id == command.entry_id
                    )
                else:
                    removed = tuple(
                        entry
                        for entry in entries
                        if command.contributor_id is None
                        or (
                            entry.added_by
                            and entry.added_by.id == command.contributor_id
                        )
                    )
                removed_ids = {entry.id for entry in removed}
                if removed and actor:
                    groups: list[RemovedGroup] = []
                    pending: list[QueueEntry] = []
                    previous = None
                    for entry in entries:
                        if entry.id in removed_ids:
                            pending.append(entry)
                        else:
                            if pending:
                                groups.append(
                                    RemovedGroup(tuple(pending), previous, entry.id)
                                )
                                pending = []
                            previous = entry.id
                    if pending:
                        groups.append(RemovedGroup(tuple(pending), previous, None))
                    captured = Removal(
                        uuid4(), actor.id, now + timedelta(seconds=12), tuple(groups)
                    )
                entries = [entry for entry in entries if entry.id not in removed_ids]
                outcome = replace(
                    outcome,
                    removed_count=len(removed),
                    entries=removed,
                    undo_id=captured.id if captured else None,
                    undo_expires_at=captured.expires_at if captured else None,
                )
            case Move(entry_id, before_id, _):
                positions = {entry.id: i for i, entry in enumerate(entries)}
                if entry_id not in positions or (
                    before_id is not None and before_id not in positions
                ):
                    raise KeyError(entry_id)
                if entry_id != before_id:
                    moved = entries.pop(positions[entry_id])
                    index = next(
                        (i for i, entry in enumerate(entries) if entry.id == before_id),
                        len(entries),
                    )
                    entries.insert(index, moved)
            case Undo(undo_id):
                if (
                    removal is None
                    or removal.id != undo_id
                    or actor is None
                    or removal.actor_id != actor.id
                    or removal.expires_at <= now
                ):
                    raise UndoUnavailable("This removal can no longer be undone.")
                restored = tuple(
                    entry for group in removal.groups for entry in group.entries
                )
                if {entry.id for entry in restored} & {entry.id for entry in entries}:
                    raise UndoUnavailable("An entry has already been restored.")
                for group in removal.groups:
                    positions = {entry.id: i for i, entry in enumerate(entries)}
                    previous_index = (
                        positions.get(group.previous_id) if group.previous_id else None
                    )
                    following = positions.get(group.next_id) if group.next_id else None
                    if (
                        previous_index is not None
                        and following is not None
                        and previous_index >= following
                    ):
                        index = len(entries)
                    elif following is not None:
                        index = following
                    elif previous_index is not None:
                        index = previous_index + 1
                    else:
                        index = len(entries)
                    entries[index:index] = group.entries
                outcome = replace(
                    outcome, restored_count=len(restored), entries=restored
                )
        result = Queue(self.session_id, tuple(entries))
        if outcome.added_count or outcome.restored_count:
            positions_by_id = {entry.id: entry for entry in result.entries}
            outcome = replace(
                outcome,
                entries=tuple(positions_by_id[entry.id] for entry in outcome.entries),
            )
        return result, outcome, captured

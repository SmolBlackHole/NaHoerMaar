# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Restore removed entries without rewinding anybody else's queue edits."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from .models import QueueEntry

UNDO_SECONDS = 12


class UndoUnavailable(ValueError):
    """The removal is expired, already restored, or belongs to another user."""


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

    @classmethod
    def capture(
        cls, queue: tuple[QueueEntry, ...], removed_ids: set[UUID], actor_id: UUID
    ) -> "Removal":
        groups: list[RemovedGroup] = []
        pending: list[QueueEntry] = []
        previous: UUID | None = None
        for entry in queue:
            if entry.id in removed_ids:
                pending.append(entry)
            else:
                if pending:
                    groups.append(RemovedGroup(tuple(pending), previous, entry.id))
                    pending = []
                previous = entry.id
        if pending:
            groups.append(RemovedGroup(tuple(pending), previous, None))
        return cls(
            uuid4(),
            actor_id,
            datetime.now(UTC) + timedelta(seconds=UNDO_SECONDS),
            tuple(groups),
        )

    @property
    def count(self) -> int:
        return sum(len(group.entries) for group in self.groups)

    def restore(self, queue: tuple[QueueEntry, ...]) -> tuple[QueueEntry, ...]:
        entries = list(queue)
        for group in self.groups:
            positions = {entry.id: index for index, entry in enumerate(entries)}
            previous = positions.get(group.previous_id) if group.previous_id else None
            following = positions.get(group.next_id) if group.next_id else None
            if previous is not None and following is not None and previous >= following:
                index = len(entries)
            elif following is not None:
                index = following
            elif previous is not None:
                index = previous + 1
            else:
                index = len(entries)
            entries[index:index] = group.entries
        return tuple(entries)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Pure queue editing rules, independent of persistence and playback effects."""

from dataclasses import replace
from uuid import UUID

from .commands import Clear, Remove
from .models import PlayerSnapshot, QueueEntry
from .undo import Removal


def enqueue(
    snapshot: PlayerSnapshot, entries: tuple[QueueEntry, ...]
) -> PlayerSnapshot:
    upcoming = snapshot.upcoming
    index = (
        next(
            (index for index, item in enumerate(upcoming) if item.origin == "radio"),
            len(upcoming),
        )
        if entries and all(item.origin == "manual" for item in entries)
        else len(upcoming)
    )
    return replace(snapshot, upcoming=(*upcoming[:index], *entries, *upcoming[index:]))


def _upcoming_entry(snapshot: PlayerSnapshot, entry_id: UUID) -> QueueEntry:
    if snapshot.current is not None and snapshot.current.id == entry_id:
        raise ValueError("The current entry is controlled by skip and stop.")
    for entry in snapshot.upcoming:
        if entry.id == entry_id:
            return entry
    raise KeyError(entry_id)


def select_removal(
    snapshot: PlayerSnapshot, command: Remove | Clear
) -> tuple[QueueEntry, ...]:
    """Select once for removal, undo capture and the public outcome."""
    if isinstance(command, Remove):
        return (_upcoming_entry(snapshot, command.entry_id),)
    return tuple(
        entry
        for entry in snapshot.upcoming
        if command.contributor_id is None
        or (entry.added_by is not None and entry.added_by.id == command.contributor_id)
    )


def remove(snapshot: PlayerSnapshot, entries: tuple[QueueEntry, ...]) -> PlayerSnapshot:
    """Remove the selected entries by identity, leaving playback untouched."""
    removed_ids = {entry.id for entry in entries}
    return replace(
        snapshot,
        upcoming=tuple(
            item for item in snapshot.upcoming if item.id not in removed_ids
        ),
    )


def move_before(
    snapshot: PlayerSnapshot, entry_id: UUID, before_entry_id: UUID | None
) -> PlayerSnapshot:
    entry = _upcoming_entry(snapshot, entry_id)
    if before_entry_id is not None:
        _upcoming_entry(snapshot, before_entry_id)
    if entry_id == before_entry_id:
        return snapshot
    upcoming = [item for item in snapshot.upcoming if item.id != entry_id]
    index = len(upcoming)
    if before_entry_id is not None:
        index = next(i for i, item in enumerate(upcoming) if item.id == before_entry_id)
    upcoming.insert(index, entry)
    return replace(snapshot, upcoming=tuple(upcoming))


def restore(snapshot: PlayerSnapshot, removal: Removal) -> PlayerSnapshot:
    return replace(snapshot, upcoming=removal.restore(snapshot.upcoming))

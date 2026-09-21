# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queue edits and undo coordination using the player's existing commit boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from uuid import UUID

from ..domain import commands, queue
from ..domain.commands import Outcome, Receipt
from ..domain.models import Contributor, PlayerSnapshot, QueueEntry
from ..domain.undo import Removal
from .catalog import PLAYLIST_LIMIT
from .player import Player


@dataclass(frozen=True, slots=True)
class Enqueue:
    entry: QueueEntry


type QueueCommand = (
    commands.Add
    | commands.AddMany
    | commands.Remove
    | commands.Move
    | commands.Clear
    | commands.Undo
)


class Queue:
    """Apply queue requests inside the caller's serialized mutation path.

    The snapshot is read on demand. Player remains the sole state/transaction
    owner; this component holds no independently writable copy of the queue.
    """

    def __init__(
        self,
        *,
        snapshot: Callable[[], PlayerSnapshot],
        entry: Callable[[str, Contributor | None], QueueEntry],
        source_key: Callable[[str], str],
        change: Callable[[Callable[[Player], PlayerSnapshot]], Awaitable[None]],
        removal: Callable[[UUID, UUID | None], Awaitable[Removal]],
    ) -> None:
        self._snapshot = snapshot
        self._entry = entry
        self._source_key = source_key
        self._change = change
        self._removal = removal

    async def request(
        self, command: QueueCommand | Enqueue, receipt: Receipt | None = None
    ) -> Outcome:
        operation: Callable[[Player], PlayerSnapshot]
        if isinstance(command, commands.Add):
            command = Enqueue(self._entry(command.source_url, command.added_by))
        match command:
            case Enqueue(entry):
                operation = partial(Player.enqueue, entry=entry)
                outcome = Outcome(entry_id=entry.id, added_count=1, entries=(entry,))
            case commands.AddMany(source_urls, added_by, skip_duplicates):
                if not 1 <= len(source_urls) <= PLAYLIST_LIMIT:
                    raise ValueError("Invalid batch size.")
                selected: list[str] = list(source_urls)
                if skip_duplicates:
                    snapshot = self._snapshot()
                    active = snapshot.upcoming + (
                        (snapshot.current,) if snapshot.current else ()
                    )
                    seen = {self._source_key(item.source_url) for item in active}
                    selected = []
                    for url in source_urls:
                        identifier = self._source_key(url)
                        if identifier not in seen:
                            selected.append(url)
                            seen.add(identifier)
                entries = tuple(self._entry(url, added_by) for url in selected)
                operation = partial(Player.enqueue_many, entries=entries)
                outcome = Outcome(
                    added_count=len(entries),
                    skipped_count=len(source_urls) - len(entries),
                    entries=entries,
                )
            case commands.Remove() | commands.Clear():
                snapshot = self._snapshot()
                removed = queue.select_removal(snapshot, command)
                operation = partial(Player.remove, entries=removed)
                outcome = Outcome(removed_count=len(removed), entries=removed)
                if removed and receipt is not None and receipt.actor_id is not None:
                    removal = Removal.capture(
                        snapshot.upcoming,
                        {item.id for item in removed},
                        receipt.actor_id,
                    )
                    receipt = replace(receipt, removal=removal)
                    outcome = replace(
                        outcome, undo_id=removal.id, undo_expires_at=removal.expires_at
                    )
            case commands.Move(entry_id, before_entry_id, _):
                operation = partial(
                    Player.move_before,
                    entry_id=entry_id,
                    before_entry_id=before_entry_id,
                )
                outcome = Outcome()
            case commands.Undo(undo_id):
                if receipt is None:
                    raise ValueError("Undo requires a request receipt.")
                removal = await self._removal(undo_id, receipt.actor_id)
                receipt = replace(receipt, consume_undo=undo_id)
                operation = partial(Player.restore_removal, removal=removal)
                outcome = Outcome(
                    restored_count=removal.count,
                    entries=tuple(
                        item for group in removal.groups for item in group.entries
                    ),
                )
        if receipt is None:
            await self._change(operation)
        else:
            outcome = replace(outcome, actor=receipt.actor)
            completed = replace(receipt, outcome=outcome)
            await self._change(
                lambda player: player.apply_request(completed, operation)
            )
        return outcome

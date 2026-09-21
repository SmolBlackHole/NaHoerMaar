# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queue acceptance without a PlaybackController, voice output or resolver."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.application.catalog import source_key
from nahormaar_backend.application.queue import Queue, QueueCommand
from nahormaar_backend.domain import commands, queue
from nahormaar_backend.domain.commands import Outcome, Receipt
from nahormaar_backend.domain.models import Contributor, PlayerSnapshot, QueueEntry
from nahormaar_backend.domain.undo import Removal
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError

VIDEO = "https://music.youtube.com/watch?v=Pqp9fDRp1lw"
OTHER = "https://youtu.be/bWHJbIm1TAA"


class QueueHarness:
    def __init__(self, player: Player) -> None:
        self.player = player
        self.queue = Queue(
            snapshot=lambda: player.snapshot,
            entry=lambda url, actor: QueueEntry(url, added_by=actor),
            source_key=source_key,
            change=self.change,
            removal=self.removal,
        )

    async def change(self, operation: Callable[[Player], PlayerSnapshot]) -> None:
        operation(self.player)

    async def removal(self, identifier: UUID, actor: UUID | None) -> Removal:
        return self.player.removal(identifier, actor)

    async def request(
        self, command: QueueCommand, actor: Contributor
    ) -> tuple[Receipt, Outcome]:
        receipt = Receipt(
            uuid4(), commands.fingerprint(command), actor_id=actor.id, actor=actor
        )
        assert self.player.reserve(receipt) is None
        outcome = await self.queue.request(command, receipt)
        return receipt, outcome


def test_queue_rules_preserve_manual_priority_and_explicit_reordering() -> None:
    radio = QueueEntry(VIDEO, origin="radio")
    manual = QueueEntry(OTHER)
    later = QueueEntry(VIDEO)
    original = PlayerSnapshot(upcoming=(radio,))
    added = queue.enqueue(original, (manual, later))
    assert original.upcoming == (radio,)
    assert added.upcoming == (manual, later, radio)
    moved = queue.move_before(added, radio.id, manual.id)
    assert moved.upcoming == (radio, manual, later)
    removed = queue.remove(
        moved, queue.select_removal(moved, commands.Remove(manual.id))
    )
    assert removed.upcoming == (radio, later)
    assert queue.move_before(removed, radio.id, None).upcoming == (later, radio)
    with pytest.raises(KeyError):
        queue.select_removal(removed, commands.Remove(manual.id))


def test_queue_requests_deduplicate_against_fresh_state_and_commit_receipts(
    player: Player, store: SQLiteStore
) -> None:
    async def scenario() -> None:
        harness = QueueHarness(player)
        actor = Contributor(uuid4(), "Listener", "0001")
        command = commands.AddMany(
            (VIDEO, "https://youtu.be/Pqp9fDRp1lw", OTHER), actor, True
        )
        before = player.revisions
        receipt, added = await harness.request(command, actor)
        assert (added.added_count, added.skipped_count) == (2, 1)
        assert added.actor == actor
        assert all(item.added_by == actor for item in added.entries)
        assert store.load().upcoming == added.entries
        assert store.reserve(receipt) == replace(receipt, outcome=added, actor=None)
        assert player.revisions.queue_revision == before.queue_revision + 1
        receipt, duplicate = await harness.request(command, actor)
        assert (duplicate.added_count, duplicate.skipped_count) == (0, 3)
        assert player.revisions.queue_revision == before.queue_revision + 1
        assert store.reserve(receipt) == replace(receipt, outcome=duplicate, actor=None)

    asyncio.run(scenario())


def test_queue_undo_preserves_other_contributors_and_commits_attribution(
    player: Player, store: SQLiteStore
) -> None:
    async def scenario() -> None:
        harness = QueueHarness(player)
        actor = Contributor(uuid4(), "Moderator", "0001")
        owner = Contributor(uuid4(), "Listener", "0002")
        mine = QueueEntry(VIDEO, added_by=owner)
        other = QueueEntry(OTHER, added_by=actor)
        player.enqueue_many((mine, other))
        _, removed = await harness.request(
            commands.Clear(player.revisions.queue_revision, owner.id), actor
        )
        assert removed.entries == (mine,) and removed.actor == actor
        assert removed.undo_id is not None
        assert player.snapshot.upcoming == (other,)
        later = QueueEntry(VIDEO, added_by=actor)
        player.enqueue(later)
        receipt, restored = await harness.request(commands.Undo(removed.undo_id), actor)
        assert restored.restored_count == 1 and restored.entries == (mine,)
        assert store.load().upcoming == (mine, other, later)
        assert store.reserve(receipt) == replace(receipt, outcome=restored, actor=None)

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["single", "contributor", "all", "none"])
def test_removal_outcome_and_undo_match_exactly_the_committed_entries(
    player: Player, store: SQLiteStore, selection: str
) -> None:
    async def scenario() -> None:
        harness = QueueHarness(player)
        owner = Contributor(uuid4(), "Listener", "0001")
        moderator = Contributor(uuid4(), "Listener", "0001")
        first = QueueEntry(VIDEO, added_by=owner)
        other = QueueEntry(VIDEO, added_by=moderator)
        anonymous = QueueEntry(VIDEO)
        last = QueueEntry(
            VIDEO, added_by=replace(owner, name="Renamed"), origin="radio"
        )
        entries = (first, other, anonymous, last)
        player.enqueue_many(entries)
        revision = player.revisions.queue_revision
        command: commands.Remove | commands.Clear
        expected: tuple[QueueEntry, ...]
        match selection:
            case "single":
                command, expected = commands.Remove(first.id), (first,)
            case "contributor":
                command, expected = commands.Clear(revision, owner.id), (first, last)
            case "all":
                command, expected = commands.Clear(revision), entries
            case _:
                command, expected = commands.Clear(revision, uuid4()), ()
        receipt, outcome = await harness.request(command, moderator)
        assert outcome.entries == expected
        assert outcome.removed_count == len(expected)
        assert outcome.actor == moderator
        assert player.snapshot == store.load()
        assert player.snapshot.upcoming == tuple(
            item for item in entries if item not in expected
        )
        assert player.revisions.queue_revision == revision + bool(expected)
        replay = store.reserve(receipt)
        assert replay is not None and replay.outcome == outcome
        if expected:
            assert outcome.undo_id is not None
            removal = player.removal(outcome.undo_id, moderator.id)
            assert (
                tuple(item for group in removal.groups for item in group.entries)
                == expected
            )
            _, restored = await harness.request(
                commands.Undo(outcome.undo_id), moderator
            )
            assert restored.entries == expected
            assert player.snapshot.upcoming == entries
            assert player.snapshot == store.load()
        else:
            assert outcome.undo_id is None and outcome.undo_expires_at is None

    asyncio.run(scenario())


def test_invalid_removal_does_not_complete_its_receipt(
    player: Player, store: SQLiteStore
) -> None:
    async def scenario() -> None:
        harness = QueueHarness(player)
        command = commands.Remove(uuid4())
        receipt = Receipt(uuid4(), commands.fingerprint(command), actor_id=uuid4())
        player.reserve(receipt)
        before, revisions = player.snapshot, player.revisions
        with pytest.raises(KeyError):
            await harness.queue.request(command, receipt)
        assert player.snapshot == store.load() == before
        assert player.revisions == revisions
        assert store.reserve(receipt) == receipt

    asyncio.run(scenario())


def test_failed_queue_commit_exposes_neither_success_nor_state_change(
    player: Player, store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        harness = QueueHarness(player)
        entry = QueueEntry(VIDEO)
        player.enqueue(entry)
        before, revisions = player.snapshot, player.revisions
        command = commands.Remove(entry.id)
        receipt = Receipt(uuid4(), commands.fingerprint(command), actor_id=uuid4())
        player.reserve(receipt)

        def fail(*args: object, **kwargs: object) -> None:
            raise StorageError("Simulated disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "save", fail)
            with pytest.raises(StorageError):
                await harness.queue.request(command, receipt)
        assert player.snapshot == before == store.load()
        assert player.revisions == revisions
        assert store.reserve(receipt) == receipt

    asyncio.run(scenario())

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.application.player import Player
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.commands import Outcome, Receipt
from nahormaar_backend.domain.models import Contributor, QueueEntry
from nahormaar_backend.domain.undo import Removal
from nahormaar_backend.persistence.database import database_engine
from nahormaar_backend.persistence.models import UndoRow
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_api import VIDEO, Harness, headers, mutation
from test_playback import ControlledResolver, FakeVoice


def test_restore_uses_surviving_neighbors_and_preserves_other_edits() -> None:
    a, b, c, d, added = [QueueEntry(VIDEO, title=str(i)) for i in range(5)]
    removal = Removal.capture((a, b, c, d), {b.id, c.id}, uuid4())
    assert removal.restore((a, added, d)) == (a, added, b, c, d)
    assert removal.restore((d, a)) == (d, a, b, c)
    assert removal.restore((added,)) == (added, b, c)
    assert removal.restore((a,)) == (a, b, c)
    assert removal.restore((d,)) == (b, c, d)
    all_removed = Removal.capture((a, b, c, d), {a.id, b.id, c.id, d.id}, uuid4())
    assert all_removed.restore((added,)) == (added, a, b, c, d)


def test_bulk_duplicates_are_checked_at_insertion_and_replayed(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = await PlaybackController.create(
            tmp_path / "queue.db", ControlledResolver(), FakeVoice()
        )
        try:
            actor = uuid4()
            original = commands.AddMany(
                (
                    VIDEO,
                    "https://music.youtube.com/watch?v=Pqp9fDRp1lw",
                    "https://youtu.be/bWHJbIm1TAA",
                ),
                skip_duplicates=True,
            )
            first, second = await asyncio.gather(
                *(
                    controller.request(uuid4(), original, actor_id=actor)
                    for _ in range(2)
                )
            )
            assert (first.outcome.added_count, first.outcome.skipped_count) == (2, 1)
            assert (second.outcome.added_count, second.outcome.skipped_count) == (0, 3)
            assert len(controller.snapshot.upcoming) == 2
            key = uuid4()
            result = await controller.request(key, original, actor_id=actor)
            await controller.clear()
            replay = await controller.request(key, original, actor_id=actor)
            assert replay.replayed and replay.outcome == result.outcome
            assert not controller.snapshot.upcoming
            repeats = await controller.request(
                uuid4(), replace(original, skip_duplicates=False), actor_id=actor
            )
            assert repeats.outcome.added_count == 3
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_duplicates_include_the_current_track(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = await PlaybackController.create(
            tmp_path / "queue.db", ControlledResolver(), FakeVoice()
        )
        try:
            await controller.enqueue(QueueEntry(VIDEO))
            await controller.connect(7)
            await controller.play()
            result = await controller.request(
                uuid4(), commands.AddMany((VIDEO,), skip_duplicates=True)
            )
            assert result.outcome.added_count == 0 and result.outcome.skipped_count == 1
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("clear", [False, True])
def test_undo_is_owned_single_use_and_survives_restart(
    tmp_path: Path, clear: bool
) -> None:
    async def scenario() -> None:
        path, actor = tmp_path / "queue.db", uuid4()
        owner = Contributor(uuid4(), "Original author", "0001")
        entry = QueueEntry(
            VIDEO, title="Original title", duration_seconds=42, added_by=owner
        )
        controller = await PlaybackController.create(
            path, ControlledResolver(), FakeVoice()
        )
        await controller.enqueue(entry)
        command = (
            commands.Clear(controller.status.queue_revision)
            if clear
            else commands.Remove(entry.id)
        )
        removal_key = uuid4()
        remover = Contributor(actor, "Kai", "0002")
        before_removal = datetime.now(UTC)
        removed = await controller.request(
            removal_key, command, actor_id=actor, actor=remover
        )
        undo_id = removed.outcome.undo_id
        assert undo_id is not None and removed.outcome.removed_count == 1
        assert removed.outcome.actor == remover
        assert removed.outcome.entries == (entry,)
        assert removed.outcome.undo_expires_at is not None
        assert (removed.outcome.undo_expires_at - before_removal).total_seconds() >= 12
        assert (
            removed.outcome.undo_expires_at - datetime.now(UTC)
        ).total_seconds() <= 12
        await controller.close()
        controller = await PlaybackController.create(
            path, ControlledResolver(), FakeVoice()
        )
        try:
            later = QueueEntry(VIDEO, title="Added later")
            await controller.enqueue(later)
            replay = await controller.request(
                removal_key,
                command,
                actor_id=actor,
                actor=replace(remover, name="New name"),
            )
            assert replay.replayed and replay.outcome == removed.outcome
            denied = await controller.request(
                uuid4(), commands.Undo(undo_id), actor_id=uuid4()
            )
            assert denied.outcome.code == "undo_unavailable"
            undo_key = uuid4()
            restored, repeated = await asyncio.gather(
                *(
                    controller.request(undo_key, commands.Undo(undo_id), actor_id=actor)
                    for _ in range(2)
                )
            )
            assert restored.outcome.restored_count == 1 and repeated.replayed
            assert controller.snapshot.upcoming == (later, entry)
            used = await controller.request(
                uuid4(), commands.Undo(undo_id), actor_id=actor
            )
            assert used.outcome.code == "undo_unavailable"
            assert controller.snapshot.upcoming == (later, entry)
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_expiry_is_server_enforced_and_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path, actor = tmp_path / "queue.db", uuid4()
        controller = await PlaybackController.create(
            path, ControlledResolver(), FakeVoice()
        )
        try:
            entry = QueueEntry(VIDEO)
            await controller.enqueue(entry)
            removed = await controller.request(
                uuid4(), commands.Remove(entry.id), actor_id=actor
            )
            assert removed.outcome.undo_expires_at and removed.outcome.undo_id
            expired_at = removed.outcome.undo_expires_at.timestamp() + 1
            monkeypatch.setattr(
                "nahormaar_backend.persistence.player_store.time",
                lambda: expired_at,
            )
            expired = await controller.request(
                uuid4(), commands.Undo(removed.outcome.undo_id), actor_id=actor
            )
            assert (
                expired.outcome.code == "undo_unavailable"
                and not controller.snapshot.upcoming
            )
            engine = database_engine(path)
            try:
                with Session(engine) as session:
                    assert session.scalar(select(UndoRow)) is None
            finally:
                engine.dispose()
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("restoring", [False, True])
def test_undo_and_queue_commit_or_roll_back_together(
    tmp_path: Path, restoring: bool
) -> None:
    with SQLiteStore(tmp_path / "queue.db") as store:
        player = Player(store)
        entry = QueueEntry(VIDEO)
        actor = uuid4()
        player.enqueue(entry)
        removal = Removal.capture((entry,), {entry.id}, actor)
        receipt = Receipt(
            uuid4(),
            "remove",
            Outcome(removed_count=1, undo_id=removal.id),
            actor,
            removal,
        )
        player.reserve(receipt)
        if restoring:
            player.apply_request(receipt, lambda p: p.remove(entry.id))
            receipt = Receipt(
                uuid4(),
                "undo",
                Outcome(restored_count=1),
                actor,
                consume_undo=removal.id,
            )
            player.reserve(receipt)
        before, revisions = player.snapshot, player.revisions

        def fail_commit(*args: object) -> None:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("COMMIT", {}, RuntimeError("disk failure"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            with pytest.raises(StorageError):
                player.apply_request(
                    receipt,
                    lambda p: (
                        p.restore_removal(removal) if restoring else p.remove(entry.id)
                    ),
                )
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert player.snapshot == before == store.load()
        assert player.revisions == revisions
        stored_receipt = store.reserve(receipt)
        assert stored_receipt is not None and stored_receipt.outcome is None
        engine = database_engine(tmp_path / "queue.db")
        try:
            with Session(engine) as session:
                assert (session.get(UndoRow, removal.id) is not None) == restoring
        finally:
            engine.dispose()


def test_undo_api_returns_counts_and_restores_attribution(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "queue.db").client() as client:
            added = mutation(
                await client.post(
                    "/api/queue", json={"source_url": VIDEO}, headers=headers()
                )
            )
            entry = added.snapshot.upcoming[0]
            assert added.entries == (entry,)
            assert added.actor == entry.added_by
            removed = mutation(
                await client.delete(f"/api/queue/{entry.id}", headers=headers())
            )
            assert removed.removed_count == 1 and removed.undo_expires_at
            assert removed.entries == (entry,)
            assert removed.actor is not None and removed.actor.name == "Andrey"
            restored = mutation(
                await client.post(
                    "/api/queue/undo",
                    json={"undo_id": str(removed.undo_id)},
                    headers=headers(),
                )
            )
            assert restored.restored_count == 1 and restored.snapshot.upcoming == (
                entry,
            )
            assert restored.entries == (entry,) and restored.actor == removed.actor

    asyncio.run(scenario())


def test_default_batch_fingerprint_is_unchanged() -> None:
    assert (
        commands.fingerprint(commands.AddMany((VIDEO,)), authenticated=True)
        == f'["AddMany", {{"source_urls": ["{VIDEO}"]}}]'
    )


def test_contributor_undo_preserves_playback_and_other_users_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        actor, author = uuid4(), Contributor(uuid4(), "Kai", "0001")
        controller = await PlaybackController.create(
            tmp_path / "queue.db", ControlledResolver(), FakeVoice()
        )
        try:
            current = QueueEntry(VIDEO)
            first, second = [QueueEntry(VIDEO, added_by=author) for _ in range(2)]
            other, later = QueueEntry(VIDEO), QueueEntry(VIDEO)
            await controller.enqueue(current)
            await controller.connect(7)
            await controller.play()
            for entry in (first, other, second):
                await controller.enqueue(entry)
            history = controller.snapshot.recently_played
            playback_id = controller.status.attempt_id
            removed = await controller.request(
                uuid4(),
                commands.Clear(controller.status.queue_revision, author.id),
                actor_id=actor,
            )
            assert removed.outcome.removed_count == 2 and removed.outcome.undo_id
            removed_state = controller.snapshot
            assert removed_state.upcoming == (other,)
            await controller.enqueue(later)
            restored = await controller.request(
                uuid4(), commands.Undo(removed.outcome.undo_id), actor_id=actor
            )
            assert restored.outcome.restored_count == 2
            assert controller.snapshot.upcoming == (first, other, second, later)
            assert controller.snapshot.current == current
            assert controller.snapshot.recently_played == history
            assert controller.status.attempt_id == playback_id
        finally:
            await controller.close()

    asyncio.run(scenario())

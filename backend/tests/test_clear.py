# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.domain.commands import Clear, fingerprint
from nahormaar_backend.domain.models import Contributor, QueueEntry
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_api import VIDEO, Harness, headers, mutation


def test_clear_by_profile_preserves_current_history_and_other_people(
    player: Player,
    store: SQLiteStore,
) -> None:
    owner = Contributor(uuid4(), "Same name", "0001")
    other = Contributor(uuid4(), "Same name", "0001")
    current = QueueEntry(VIDEO, added_by=owner)
    mine = QueueEntry(VIDEO, added_by=owner)
    renamed = QueueEntry(VIDEO, added_by=replace(owner, name="New name"))
    theirs = QueueEntry(VIDEO, added_by=other)
    anonymous = QueueEntry(VIDEO)
    player.enqueue_many((current, theirs, mine, anonymous, renamed))
    player.play()
    player.mark_playing()
    before = player.snapshot
    revision = player.revisions
    cleared = player.clear(owner.id)
    assert cleared.current == current
    assert cleared.state == before.state
    assert cleared.recently_played == before.recently_played
    assert cleared.upcoming == (theirs, anonymous)
    assert player.revisions.queue_revision == revision.queue_revision + 1
    assert store.load() == cleared
    unchanged = player.revisions
    assert player.clear(uuid4()) == cleared
    assert player.revisions == unchanged


def test_profile_clear_storage_failure_does_not_publish_partial_removal(
    player: Player,
    store: SQLiteStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = Contributor(uuid4(), "Owner", "0001")
    player.enqueue_many(tuple(QueueEntry(VIDEO, added_by=owner) for _ in range(3)))
    before, revisions = player.snapshot, player.revisions

    def fail(*args: object, **kwargs: object) -> None:
        raise StorageError("Disk unavailable")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(StorageError):
        player.clear(owner.id)
    assert player.snapshot == before == store.load()
    assert player.revisions == revisions


def test_whole_queue_clear_keeps_existing_receipt_fingerprints() -> None:
    assert fingerprint(Clear(3)) == '["Clear", {"expected_queue_revision": 3}]'
    assert fingerprint(Clear(3, uuid4())) != fingerprint(Clear(3))


def test_profile_clear_is_atomic_conflict_checked_and_replayable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        owner, other = str(uuid4()), str(uuid4())
        key = headers()
        async with harness.client() as client:

            async def add(profile_id: str) -> None:
                assert harness.controller is not None
                await harness.controller.enqueue(
                    QueueEntry(
                        VIDEO,
                        added_by=Contributor(UUID(profile_id), "Same name", "0002"),
                    )
                )

            await add(owner)
            await add(other)
            await add(owner)
            assert harness.controller is not None
            async with harness.controller.subscribe() as updates:
                before = await updates.get()
                assert before is not None
                body = {
                    "expected_queue_revision": before.queue_revision,
                    "contributor_id": owner,
                }
                result = mutation(
                    await client.post("/api/queue/clear", json=body, headers=key)
                )
                assert len(result.snapshot.upcoming) == 1
                assert result.snapshot.upcoming[0].added_by is not None
                assert str(result.snapshot.upcoming[0].added_by.id) == other
                assert result.snapshot.queue_revision == before.queue_revision + 1
                after = await updates.get()
                assert after is not None and len(after.player.upcoming) == 1
                assert updates.empty()
                # A retry must not remove tracks added after the original action.
                await add(owner)
                replay = mutation(
                    await client.post("/api/queue/clear", json=body, headers=key)
                )
                assert replay.replayed and len(replay.snapshot.upcoming) == 2
                stale = mutation(
                    await client.post("/api/queue/clear", json=body, headers=headers()),
                    409,
                )
                assert (
                    stale.code == "queue_conflict" and len(stale.snapshot.upcoming) == 2
                )
                changed = mutation(
                    await client.post(
                        "/api/queue/clear",
                        json={**body, "contributor_id": other},
                        headers=key,
                    ),
                    409,
                )
                assert changed.code == "idempotency_conflict"
        async with harness.client() as client:
            replay = mutation(
                await client.post("/api/queue/clear", json=body, headers=key)
            )
            assert replay.replayed and len(replay.snapshot.upcoming) == 2
            all_tracks = mutation(
                await client.post(
                    "/api/queue/clear",
                    json={"expected_queue_revision": replay.snapshot.queue_revision},
                    headers=headers(),
                )
            )
            assert not all_tracks.snapshot.upcoming

    asyncio.run(scenario())


def test_invalid_profile_clear_never_falls_back_to_clear_all(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
            added = mutation(
                await client.post(
                    "/api/queue", json={"source_url": VIDEO}, headers=headers()
                )
            )
            for identifier in ("", "not-a-uuid", 42):
                response = await client.post(
                    "/api/queue/clear",
                    headers=headers(),
                    json={
                        "expected_queue_revision": added.snapshot.queue_revision,
                        "contributor_id": identifier,
                    },
                )
                assert response.status_code == 422
            assert len((await client.get("/api/state")).json()["upcoming"]) == 1

    asyncio.run(scenario())

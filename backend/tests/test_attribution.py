# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.commands import Add, Outcome, Receipt, fingerprint
from nahormaar_backend.auth import SESSION_COOKIE, csrf_token
from nahormaar_backend.models import (
    ANONYMOUS_CONTRIBUTOR,
    Contributor,
    QueueEntry,
    TrackMetadata,
)
from nahormaar_backend.player import Player
from nahormaar_backend.storage import SQLiteStore
from test_api import VIDEO, Harness, headers, mutation


def test_concurrent_contributors_survive_retries_and_restart(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "player.sqlite3")
    bob_headers = {
        "Cookie": f"{SESSION_COOKIE}={'b' * 43}",
        "X-CSRF-Token": csrf_token("b" * 43),
    }

    async def scenario() -> None:
        key = headers()
        async with harness.client() as client:
            harness.account("2", "b" * 43, "Bob")
            assert (
                await client.put(
                    "/api/profile", json={"name": "Alice", "avatar": "0002"}
                )
            ).status_code == 200
            replies = await asyncio.gather(
                client.post(
                    "/api/queue",
                    json={"source_url": VIDEO},
                    headers=key,
                ),
                client.post(
                    "/api/queue",
                    json={"source_url": VIDEO},
                    headers=headers() | bob_headers,
                ),
            )
            added = mutation(replies[0])
            mutation(replies[1])
            replay = mutation(
                await client.post(
                    "/api/queue",
                    json={"source_url": VIDEO},
                    headers=key,
                )
            )
            assert replay.replayed and replay.entry_id == added.entry_id
            assert {
                entry.added_by.name
                for entry in replay.snapshot.upcoming
                if entry.added_by
            } == {"Alice", "Bob"}
            conflict = mutation(
                await client.post(
                    "/api/queue",
                    json={"source_url": VIDEO},
                    headers=key | bob_headers,
                ),
                409,
            )
            assert conflict.code == "idempotency_conflict"
        async with harness.client() as client:
            snapshot = (await client.get("/api/state")).json()
            assert {entry["added_by"]["name"] for entry in snapshot["upcoming"]} == {
                "Alice",
                "Bob",
            }
            replay = mutation(
                await client.post(
                    "/api/queue",
                    json={"source_url": VIDEO},
                    headers=key,
                )
            )
            assert replay.replayed and len(replay.snapshot.upcoming) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad"),
        ("name", "  "),
        ("name", "a" * 33),
        ("avatar", "../image"),
        ("avatar", "12345"),
    ],
)
def test_invalid_contributors_cannot_change_queue(
    tmp_path: Path, field: str, value: str
) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        profile = {
            "id": str(UUID(int=1)),
            "name": "Alice",
            "avatar": "00af",
            field: value,
        }
        async with harness.client() as client:
            reply = await client.post(
                "/api/queue",
                json={"source_url": VIDEO, "added_by": profile},
                headers=headers(),
            )
            assert reply.status_code == 422
            assert (await client.get("/api/state")).json()["upcoming"] == []

    asyncio.run(scenario())


def test_metadata_history_and_recovery_preserve_original_contributor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "player.sqlite3"
    alice = Contributor(UUID(int=1), "Alice", "00af")
    bob = Contributor(UUID(int=2), "Bob", "10bd")
    with SQLiteStore(path) as store:
        player = Player(store)
        entry = QueueEntry(VIDEO, added_by=alice)
        player.enqueue(entry)
        player.enrich(entry.id, TrackMetadata(title="Song", artist="Artist"))
        player.play()
        player.mark_playing()
        player.skip()
        player.enqueue(QueueEntry(VIDEO, added_by=bob))
    with SQLiteStore(path) as store:
        snapshot = Player(store).snapshot
        assert snapshot.upcoming[0].added_by == bob
        assert snapshot.recently_played[0].entry.added_by == alice
        assert snapshot.recently_played[0].entry.title == "Song"


def test_unattributed_requests_keep_existing_fingerprints() -> None:
    assert fingerprint(Add(VIDEO)) == json.dumps(
        ["Add", {"source_url": VIDEO}], sort_keys=True
    )
    assert fingerprint(Add(VIDEO, ANONYMOUS_CONTRIBUTOR)) == fingerprint(Add(VIDEO))


def test_existing_anonymous_receipt_cannot_be_claimed_after_upgrade(
    tmp_path: Path,
) -> None:
    path = tmp_path / "player.sqlite3"
    url = "https://www.youtube.com/watch?v=Pqp9fDRp1lw"
    entry = QueueEntry(url)
    request_id = uuid4()
    with SQLiteStore(path) as store:
        player = Player(store)
        receipt = Receipt(
            request_id,
            json.dumps(["Add", {"source_url": url}], sort_keys=True),
            Outcome(entry_id=entry.id),
        )
        player.reserve(receipt)
        player.apply_request(receipt, lambda active: active.enqueue(entry))

    async def scenario() -> None:
        async with Harness(path).client() as client:
            replay = mutation(
                await client.post(
                    "/api/queue",
                    json={"source_url": url},
                    headers={"Idempotency-Key": str(request_id)},
                ),
                409,
            )
            assert not replay.replayed and replay.code == "idempotency_conflict"
            assert len(replay.snapshot.upcoming) == 1
            new = mutation(
                await client.post(
                    "/api/queue", json={"source_url": url}, headers=headers()
                )
            )
            contributor = new.snapshot.upcoming[-1].added_by
            assert contributor is not None and contributor.name == "Andrey"

    asyncio.run(scenario())

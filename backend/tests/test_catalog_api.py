# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend import catalog as module
from nahormaar_backend.api_models import State
from nahormaar_backend.catalog import MediaCatalog
from nahormaar_backend.audio import ResolvedTrack
from nahormaar_backend.models import PlaybackState
from nahormaar_backend.search import SearchSource
from nahormaar_backend.storage import SQLiteStore
from test_api import Harness, headers, mutation
from test_catalog import PLAYLIST, VIDEO, Runner, item
from test_commands import wait_for


def test_search_defaults_to_music_and_validates_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.entries = [
            {"videoId": "Pqp9fDRp1lw", "title": "Music result", "duration_seconds": 180}
        ]
        monkeypatch.setattr(module, "run_process", runner)
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            assert harness.controller is not None
            harness.controller.catalog = MediaCatalog(Path("node"))
            response = await client.get("/api/catalog/search", params={"q": "song"})
            assert response.status_code == 200
            assert (
                response.json()["entries"][0]["source_url"]
                == "https://music.youtube.com/watch?v=Pqp9fDRp1lw"
            )
            assert "nahormaar_backend.music_search" in runner.calls[0]
            assert (
                await client.get(
                    "/api/catalog/search", params={"q": "song", "source": "spotify"}
                )
            ).status_code == 422
            channels = await client.get("/api/channels")
            assert channels.json()[0]["guild_id"] == "1"
            assert channels.json()[0]["guild_name"] == "Test server"

    asyncio.run(scenario())


def test_playback_starts_while_discovery_slots_are_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.release = asyncio.Event()
        monkeypatch.setattr(module, "run_process", runner)
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            assert harness.controller is not None
            controller = harness.controller
            library = MediaCatalog(Path("node"))
            controller.catalog = library
            tasks = [asyncio.create_task(library.search(str(i))) for i in range(2)]
            try:
                await wait_for(lambda: len(runner.calls) == 2)
                mutation(
                    await client.post(
                        "/api/queue", json={"source_url": VIDEO}, headers=headers()
                    )
                )
                await controller.connect(7)
                await controller.play()
                await wait_for(lambda: len(harness.resolver.requests) == 1)
                harness.resolver.requests[0].set_result(
                    ResolvedTrack("https://stream.invalid/audio", duration_seconds=180)
                )
                await wait_for(
                    lambda: controller.snapshot.state is PlaybackState.PLAYING
                )
                assert all(not task.done() for task in tasks)
            finally:
                runner.release.set()
                await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_batch_is_atomic_ordered_and_replayed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Harness(tmp_path / "player.sqlite3")
    key = headers()
    body = {
        "source_urls": [VIDEO, "https://youtu.be/bWHJbIm1TAA", VIDEO],
        "added_by": {"id": str(uuid4()), "name": "Andrey", "avatar": "0001"},
    }

    async def scenario() -> None:
        runner = Runner()
        runner.entries = [item(), item(id="bWHJbIm1TAA", duration=None)]
        monkeypatch.setattr(module, "run_process", runner)
        async with harness.client() as client:
            assert harness.controller is not None
            library = MediaCatalog(Path("node"))
            harness.controller.catalog = library
            await library.search("example", source=SearchSource.VIDEOS)
            before = State.model_validate((await client.get("/api/state")).json())
            batch_response, single_response = await asyncio.gather(
                client.post("/api/queue/batch", json=body, headers=key),
                client.post(
                    "/api/queue", json={"source_url": VIDEO}, headers=headers()
                ),
            )
            batch, single = mutation(batch_response), mutation(single_response)
            assert batch.entry_id is None
            state = State.model_validate((await client.get("/api/state")).json())
            assert len(state.upcoming) == 4
            assert state.queue_revision == before.queue_revision + 2
            assert state.revision == before.revision + 2
            imported = [
                entry for entry in state.upcoming if entry.id != single.entry_id
            ]
            assert [entry.video_id for entry in imported] == [
                "Pqp9fDRp1lw",
                "bWHJbIm1TAA",
                "Pqp9fDRp1lw",
            ]
            positions = [state.upcoming.index(entry) for entry in imported]
            assert positions == list(range(positions[0], positions[0] + 3))
            assert len({entry.id for entry in imported}) == 3
            assert all(
                entry.added_by and entry.added_by.name == "Andrey" for entry in imported
            )
            assert imported[0].title == "Амура - Я хочу любить"
            assert imported[1].duration_seconds is None
            assert harness.resolver.calls == []
            replay = mutation(
                await client.post("/api/queue/batch", json=body, headers=key)
            )
            assert replay.replayed and replay.snapshot.revision == state.revision
            conflict = mutation(
                await client.post(
                    "/api/queue/batch", json={"source_urls": [VIDEO]}, headers=key
                ),
                409,
            )
            assert conflict.code == "idempotency_conflict"
        async with harness.client() as client:
            restarted = mutation(
                await client.post("/api/queue/batch", json=body, headers=key)
            )
            assert restarted.replayed and len(restarted.snapshot.upcoming) == 4

    asyncio.run(scenario())


def test_invalid_batch_never_commits_a_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
            before = State.model_validate((await client.get("/api/state")).json())
            for sources in (
                [],
                [VIDEO] * 101,
                [VIDEO, PLAYLIST],
                [VIDEO, "https://example.org"],
            ):
                result = await client.post(
                    "/api/queue/batch", json={"source_urls": sources}, headers=headers()
                )
                assert result.status_code == 422
            state = State.model_validate((await client.get("/api/state")).json())
            assert state == before

    asyncio.run(scenario())


def test_batch_storage_failure_leaves_no_partial_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nahormaar_backend.storage import StorageError

    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            original = SQLiteStore.save

            def fail(*args: object, **kwargs: object) -> None:
                raise StorageError("Disk unavailable")

            monkeypatch.setattr(SQLiteStore, "save", fail)
            key = headers()
            body = {"source_urls": [VIDEO, VIDEO]}
            result = await client.post("/api/queue/batch", json=body, headers=key)
            assert result.status_code == 503
            monkeypatch.setattr(SQLiteStore, "save", original)
        async with harness.client() as client:
            assert not State.model_validate(
                (await client.get("/api/state")).json()
            ).upcoming
            retry = mutation(
                await client.post("/api/queue/batch", json=body, headers=key), 409
            )
            assert retry.replayed and retry.code == "interrupted"
            assert not retry.snapshot.upcoming
            fresh = mutation(
                await client.post("/api/queue/batch", json=body, headers=headers())
            )
            assert len(fresh.snapshot.upcoming) == 2

    asyncio.run(scenario())


def test_batch_publishes_one_complete_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            assert harness.controller is not None
            async with harness.controller.subscribe() as updates:
                before = await updates.get()
                assert before is not None
                key = headers()
                body = {"source_urls": [VIDEO] * 100}
                replies = await asyncio.gather(
                    *(
                        client.post("/api/queue/batch", json=body, headers=key)
                        for _ in range(2)
                    )
                )
                assert sorted(mutation(reply).replayed for reply in replies) == [
                    False,
                    True,
                ]
                after = await updates.get()
                assert after is not None
                assert len(after.player.upcoming) == 100
                assert after.revision == before.revision + 1
                assert updates.empty()

    asyncio.run(scenario())


def test_preview_api_progress_cancel_and_queue_independence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        runner = Runner()
        runner.entries = [item(), item(id="bWHJbIm1TAA", availability="private")]
        runner.release = asyncio.Event()
        monkeypatch.setattr(module, "run_process", runner)
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            assert harness.controller is not None
            library = MediaCatalog(Path("node"))
            harness.controller.catalog = library
            key = headers()
            response = await client.post(
                "/api/youtube/playlists", json={"source_url": PLAYLIST}, headers=key
            )
            assert response.status_code == 202
            identifier = response.json()["id"]
            await runner.started.wait()
            progress = await client.get(f"/api/youtube/playlists/{identifier}")
            assert progress.json()["state"] == "loading"
            assert progress.json()["entries"][1]["unavailable"]
            added = mutation(
                await client.post(
                    "/api/queue", json={"source_url": VIDEO}, headers=headers()
                )
            )
            assert len(added.snapshot.upcoming) == 1
            cancelled = await client.delete(f"/api/youtube/playlists/{identifier}")
            assert cancelled.json()["state"] == "cancelled" and runner.cancelled
            assert (
                await client.delete(f"/api/youtube/playlists/{identifier}")
            ).json() == cancelled.json()
            state = State.model_validate((await client.get("/api/state")).json())
            assert state.revision == added.snapshot.revision
            assert (
                await client.get(f"/api/youtube/playlists/{uuid4()}")
            ).status_code == 410
            assert (
                await client.post(
                    "/api/youtube/playlists",
                    json={"source_url": VIDEO},
                    headers=headers(),
                )
            ).status_code == 422
            runner.release.set()
            search = await client.get(
                "/api/catalog/search", params={"q": "Амура", "source": "youtube"}
            )
            assert (
                search.status_code == 200
                and search.json()["entries"][0]["title"] == "Амура - Я хочу любить"
            )
            assert search.json()["next_offset"] is None
            for offset in (-10, 1, 100, "invalid"):
                assert (
                    await client.get(
                        "/api/catalog/search", params={"q": "music", "offset": offset}
                    )
                ).status_code == 422
            assert (
                await client.get("/api/catalog/search", params={"q": " "})
            ).status_code == 422
            assert (
                await client.get("/api/catalog/search", params={"q": "x" * 201})
            ).status_code == 422
            await wait_for(lambda: len(runner.calls) == 2)

    asyncio.run(scenario())

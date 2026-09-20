# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import socket
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from nahormaar_backend.__main__ import LocalServer
from nahormaar_backend.api import create_app
from nahormaar_backend.api.schemas import Channel, MutationResult, State
from nahormaar_backend.application.audio import ResolvedTrack
from nahormaar_backend.application.auth import SESSION_COOKIE, csrf_token, digest
from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.config import AuthSettings
from nahormaar_backend.domain.accounts import Account
from nahormaar_backend.domain.models import PlaybackState, QueueEntry
from nahormaar_backend.persistence.accounts import Accounts
from test_commands import wait_for
from test_playback import ControlledResolver, FakeVoice

VIDEO = "https://music.youtube.com/watch?v=Pqp9fDRp1lw"
TEST_TOKEN = "a" * 43
TEST_ORIGIN = "http://127.0.0.1:8000"
AUTH_HEADERS = {"Origin": TEST_ORIGIN, "X-CSRF-Token": csrf_token(TEST_TOKEN)}


class Harness:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.voice = FakeVoice()
        self.resolver = ControlledResolver()
        self.controller: PlaybackController | None = None
        self.starts = 0
        self.stops = 0
        self.auth_settings = AuthSettings(
            TEST_ORIGIN, "123", "test-secret", path, path.with_suffix(".toml")
        )
        self.auth_settings.access_path.write_text(
            'discord_ids = ["1", "2"]', encoding="utf-8"
        )

    def account(
        self, identifier: str = "1", token: str = TEST_TOKEN, name: str = "Andrey"
    ) -> Account:
        accounts = Accounts(self.path)
        try:
            return accounts.create_session(
                identifier,
                name,
                "0002",
                digest(token),
                time.time() + 3600,
                time.time(),
                digest(token),
            )
        finally:
            accounts.close()

    @asynccontextmanager
    async def runtime(self) -> AsyncGenerator[PlaybackController]:
        self.starts += 1
        self.controller = await PlaybackController.create(
            self.path, self.resolver, self.voice
        )
        self.account()
        try:
            yield self.controller
        finally:
            await self.controller.close()
            self.stops += 1

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[httpx.AsyncClient]:
        app = create_app(self.runtime, auth_settings=self.auth_settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                cookies={SESSION_COOKIE: TEST_TOKEN},
                headers=AUTH_HEADERS,
            ) as client,
        ):
            yield client


def mutation(response: httpx.Response, status_code: int = 200) -> MutationResult:
    assert response.status_code == status_code, response.text
    return MutationResult.model_validate(response.json())


def headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def test_api_queue_contracts_and_retries(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "player.sqlite3")

    async def scenario() -> None:
        async with harness.client() as client:
            key = headers()
            added = mutation(
                await client.post("/api/queue", json={"source_url": VIDEO}, headers=key)
            )
            assert added.entry_id is not None
            assert (
                added.snapshot.upcoming[0].source_url
                == "https://www.youtube.com/watch?v=Pqp9fDRp1lw"
            )
            replay = mutation(
                await client.post("/api/queue", json={"source_url": VIDEO}, headers=key)
            )
            assert replay.replayed and replay.entry_id == added.entry_id
            assert replay.snapshot.revision == added.snapshot.revision
            conflict = mutation(
                await client.post(
                    "/api/queue",
                    json={"source_url": "https://youtu.be/bWHJbIm1TAA"},
                    headers=key,
                ),
                409,
            )
            assert conflict.code == "idempotency_conflict"
            stale = mutation(
                await client.post(
                    "/api/queue/clear",
                    json={"expected_queue_revision": 0},
                    headers=headers(),
                ),
                409,
            )
            assert stale.code == "queue_conflict"
            assert len(stale.snapshot.upcoming) == 1
            missing = mutation(
                await client.delete(f"/api/queue/{uuid4()}", headers=headers()), 404
            )
            assert missing.code == "entry_not_found"
            moved = mutation(
                await client.post(
                    f"/api/queue/{added.entry_id}/move",
                    json={
                        "before_entry_id": None,
                        "expected_queue_revision": added.snapshot.queue_revision,
                    },
                    headers=headers(),
                )
            )
            assert moved.snapshot.revision == added.snapshot.revision
            removed = mutation(
                await client.delete(f"/api/queue/{added.entry_id}", headers=headers())
            )
            assert removed.snapshot.upcoming == ()
            assert harness.resolver.calls == []
            schema = await client.get("/openapi.json")
            assert schema.status_code == 200
        assert harness.starts == harness.stops == 1

    asyncio.run(scenario())


def test_api_seek_validates_target_and_publishes_new_position(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "player.sqlite3")

    async def scenario() -> None:
        async with harness.client() as client:
            assert harness.controller is not None
            await harness.controller.enqueue(QueueEntry(VIDEO))
            await harness.controller.connect(7)
            await harness.controller.play()
            await wait_for(lambda: len(harness.resolver.requests) == 1)
            harness.resolver.requests[0].set_result(
                ResolvedTrack("https://stream.invalid/audio", duration_seconds=180)
            )
            await wait_for(
                lambda: (
                    harness.controller is not None
                    and harness.controller.snapshot.state is PlaybackState.PLAYING
                )
            )
            before = State.model_validate((await client.get("/api/state")).json())
            for position in (-1, "10", True, None):
                response = await client.put(
                    "/api/player/seek",
                    json={
                        "position_seconds": position,
                        "expected_playback_id": str(before.playback_id),
                    },
                    headers=headers(),
                )
                assert response.status_code == 422
            key = headers()
            body = {
                "position_seconds": 60,
                "expected_playback_id": str(before.playback_id),
            }
            result = mutation(
                await client.put("/api/player/seek", json=body, headers=key)
            )
            assert result.snapshot.position_seconds == 60
            assert result.snapshot.playback_id != before.playback_id
            assert result.snapshot.current == before.current
            assert result.snapshot.recently_played == before.recently_played
            replay = mutation(
                await client.put("/api/player/seek", json=body, headers=key)
            )
            assert replay.replayed
            assert harness.voice.positions == [0, 60]
            stale = mutation(
                await client.put("/api/player/seek", json=body, headers=headers()), 409
            )
            assert stale.code == "playback_conflict"

    asyncio.run(scenario())


def test_api_concurrent_additions_and_stale_reorder_preserve_both_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/api/queue", json={"source_url": VIDEO}, headers=headers()
                    )
                    for _ in range(2)
                )
            )
            replies = [mutation(response) for response in responses]
            state = State.model_validate((await client.get("/api/state")).json())
            assert len(state.upcoming) == 2
            assert {entry.id for entry in state.upcoming} == {
                reply.entry_id for reply in replies
            }
            stale = mutation(
                await client.post(
                    f"/api/queue/{state.upcoming[1].id}/move",
                    json={
                        "before_entry_id": str(state.upcoming[0].id),
                        "expected_queue_revision": state.queue_revision - 1,
                    },
                    headers=headers(),
                ),
                409,
            )
            assert stale.snapshot == state

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/queue", {"source_url": "file:///local"}),
        ("/api/queue", {"source_url": "https://www.youtube.com/playlist?list=abc"}),
        ("/api/queue", {"source_url": VIDEO, "title": "not accepted here"}),
        ("/api/queue/clear", {"expected_queue_revision": -1}),
        ("/api/queue/clear", {"expected_queue_revision": True}),
        ("/api/player/skip", {}),
        ("/api/player/pause", {"expected_playback_id": "invalid"}),
    ],
)
def test_invalid_inputs_are_rejected(
    tmp_path: Path, path: str, body: dict[str, object]
) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
            assert (
                await client.post(path, json=body, headers=headers())
            ).status_code == 422

    asyncio.run(scenario())


def test_player_controls_and_string_channel_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            channel_id = "1264562114140442737"
            connected = mutation(
                await client.put(
                    "/api/voice/channel",
                    json={"channel_id": channel_id},
                    headers=headers(),
                )
            )
            assert connected.snapshot.channel_id == channel_id
            channels = [
                Channel.model_validate(value)
                for value in (await client.get("/api/channels")).json()
            ]
            assert channels[0].id == "7"
            for value in (-1, 1.1, "0.5", True):
                assert (
                    await client.put(
                        "/api/player/volume", json={"volume": value}, headers=headers()
                    )
                ).status_code == 422
            volume_key = headers()
            volume = mutation(
                await client.put(
                    "/api/player/volume", json={"volume": 0.5}, headers=volume_key
                )
            )
            repeat = mutation(
                await client.put(
                    "/api/player/volume", json={"volume": 0.5}, headers=volume_key
                )
            )
            assert (
                repeat.replayed and repeat.snapshot.revision == volume.snapshot.revision
            )
            assert harness.voice.volumes == [0.5]
            await client.post(
                "/api/queue", json={"source_url": VIDEO}, headers=headers()
            )
            playing = mutation(
                await client.post(
                    "/api/player/play",
                    json={"expected_playback_id": None},
                    headers=headers(),
                )
            )
            assert playing.snapshot.playback_id is not None
            assert harness.controller is not None
            await wait_for(lambda: len(harness.resolver.requests) == 1)
            harness.resolver.succeed(0)
            await wait_for(lambda: len(harness.voice.played) == 1)
            target = {"expected_playback_id": str(playing.snapshot.playback_id)}
            paused = mutation(
                await client.post("/api/player/pause", json=target, headers=headers())
            )
            assert paused.snapshot.state == "paused"
            resumed = mutation(
                await client.post("/api/player/play", json=target, headers=headers())
            )
            assert resumed.snapshot.state == "playing"
            stopped = mutation(
                await client.post("/api/player/stop", json=target, headers=headers())
            )
            assert (
                stopped.snapshot.state == "idle" and len(stopped.snapshot.upcoming) == 1
            )
            assert "stream.invalid" not in stopped.model_dump_json()
            left = mutation(
                await client.delete("/api/voice/channel", headers=headers())
            )
            assert left.snapshot.channel_id is None

    asyncio.run(scenario())


def test_local_origin_host_and_request_id_requirements(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
            assert (
                await client.post("/api/queue", json={"source_url": VIDEO})
            ).status_code == 422
            assert (
                await client.get("/api/state", headers={"Host": "foreign.invalid"})
            ).status_code == 400
            assert (
                await client.get(
                    "/api/state", headers={"Origin": "https://foreign.invalid"}
                )
            ).status_code == 403
            assert (
                await client.get(
                    "/api/state", headers={"Origin": "http://127.0.0.1:8000"}
                )
            ).status_code == 200

    asyncio.run(scenario())


@asynccontextmanager
async def running_server(
    app: FastAPI,
    shutdown_event: asyncio.Event,
) -> AsyncGenerator[tuple[str, uvicorn.Server, asyncio.Task[None]]]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.setblocking(False)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=listener.getsockname()[1],
            log_level="warning",
            timeout_graceful_shutdown=1,
        )
        server = LocalServer(config, shutdown_event)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await wait_for(lambda: server.started or task.done())
            assert server.started
            yield f"http://127.0.0.1:{listener.getsockname()[1]}", server, task
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=3)


async def next_state(lines: AsyncIterator[str]) -> State:
    event_id: str | None = None
    state: State | None = None
    async with asyncio.timeout(3):
        async for line in lines:
            if line.startswith("id: "):
                event_id = line[4:]
            if line.startswith("data: "):
                state = State.model_validate_json(line[6:])
            if not line and state is not None:
                assert event_id == str(state.revision)
                return state
    raise AssertionError("SSE closed before a snapshot")


def test_sse_two_clients_reconnect_and_shutdown_with_open_stream(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        shutdown_event = asyncio.Event()
        async with (
            running_server(
                create_app(
                    harness.runtime,
                    shutdown_event=shutdown_event,
                    auth_settings=harness.auth_settings,
                ),
                shutdown_event,
            ) as (url, server, task),
            httpx.AsyncClient(
                base_url=url, cookies={SESSION_COOKIE: TEST_TOKEN}, headers=AUTH_HEADERS
            ) as client,
        ):
            async with (
                client.stream("GET", "/api/events") as first,
                client.stream("GET", "/api/events") as second,
            ):
                assert first.status_code == second.status_code == 200
                assert "text/event-stream" in first.headers["content-type"]
                assert first.headers["cache-control"] == "no-store"
                first_lines, second_lines = first.aiter_lines(), second.aiter_lines()
                initial = await next_state(first_lines)
                assert await next_state(second_lines) == initial
                added = mutation(
                    await client.post(
                        "/api/queue", json={"source_url": VIDEO}, headers=headers()
                    )
                )
                assert await next_state(first_lines) == added.snapshot
                assert await next_state(second_lines) == added.snapshot
            async with client.stream(
                "GET", "/api/events", headers={"Last-Event-ID": str(initial.revision)}
            ) as reconnect:
                assert await next_state(reconnect.aiter_lines()) == added.snapshot
                server.should_exit = True
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
        assert harness.starts == harness.stops == 1
        assert harness.voice.disconnect_count == 1

    asyncio.run(scenario())


def test_sse_subscription_includes_update_racing_connection(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        shutdown_event = asyncio.Event()
        async with (
            running_server(
                create_app(
                    harness.runtime,
                    shutdown_event=shutdown_event,
                    auth_settings=harness.auth_settings,
                ),
                shutdown_event,
            ) as (url, _, _),
            httpx.AsyncClient(
                base_url=url, cookies={SESSION_COOKIE: TEST_TOKEN}, headers=AUTH_HEADERS
            ) as client,
        ):
            assert harness.controller is not None
            addition = asyncio.create_task(
                harness.controller.enqueue(QueueEntry(VIDEO))
            )
            async with client.stream("GET", "/api/events") as response:
                lines = response.aiter_lines()
                state = await next_state(lines)
                await addition
                if not state.upcoming:
                    state = await next_state(lines)
                assert len(state.upcoming) == 1

    asyncio.run(scenario())

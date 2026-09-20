# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Self, cast

import pytest

import nahormaar_backend.runtime as runtime_module
from nahormaar_backend.application.audio import VoiceError
from nahormaar_backend.application.catalog import MediaCatalog
from nahormaar_backend.application.radio import RadioCatalog
from nahormaar_backend.config import Settings
from nahormaar_backend.runtime import open_runtime


class FakeResolver:
    def __init__(self, node_path: Path) -> None:
        self.node_path = node_path


class FakeController:
    instances: ClassVar[list[FakeController]] = []
    create_error: ClassVar[Exception | None] = None
    close_error: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.close_calls = 0
        self.worker_closed = False
        self.database: Path | None = None
        self.catalog: MediaCatalog | None = None
        self.radio_catalog: RadioCatalog | None = None
        self.restore_calls = 0
        type(self).instances.append(self)

    @classmethod
    async def create(
        cls,
        database: Path,
        resolver: object,
        voice: object,
        *,
        metadata_resolver: object,
        catalog: MediaCatalog,
        radio_catalog: RadioCatalog,
    ) -> Self:
        assert metadata_resolver is catalog
        del resolver, voice
        error = cls.create_error
        if error is not None:
            raise error
        instance = cls()
        instance.database = database
        instance.catalog = catalog
        instance.radio_catalog = radio_catalog
        return instance

    async def close(self) -> None:
        self.close_calls += 1
        self.worker_closed = True
        if self.catalog:
            await self.catalog.close()
        if self.radio_catalog:
            await self.radio_catalog.close()
        error = type(self).close_error
        if error is not None:
            raise error

    async def restore(self) -> None:
        assert FakeVoice.instances[0].started.is_set()
        self.restore_calls += 1


class FakeVoice:
    instances: ClassVar[list[FakeVoice]] = []
    mode: ClassVar[str] = "running"
    close_error: ClassVar[Exception | None] = None

    def __init__(self, ffmpeg_path: Path) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.start_calls = 0
        self.close_calls = 0
        self.token: str | None = None
        self.client_task: asyncio.Task[None] | None = None
        self.client_loop: asyncio.AbstractEventLoop | None = None
        self.command_access_path: Path | None = None
        self.command_connect: Callable[[int], Awaitable[object]] | None = None
        type(self).instances.append(self)

    def install_commands(
        self, access_path: Path, connect: Callable[[int], Awaitable[object]]
    ) -> None:
        assert self.start_calls == 0
        self.command_access_path = access_path
        self.command_connect = connect

    async def start(self, token: str) -> None:
        self.start_calls += 1
        self.token = token
        self.client_loop = asyncio.get_running_loop()
        self.client_task = asyncio.current_task()
        self.started.set()
        if type(self).mode == "authentication":
            raise VoiceError("Discord authentication failed.")
        if type(self).mode == "early-exit":
            return
        await self.closed.wait()

    async def wait_until_ready(self) -> None:
        await self.started.wait()
        if type(self).mode == "ready":
            raise VoiceError("Discord gateway is unavailable.")
        if type(self).mode == "authentication":
            raise VoiceError("Discord authentication failed.")

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        error = type(self).close_error
        if error is not None:
            raise error


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        token="test-token",  # noqa: S106 - synthetic test credential
        database_path=tmp_path / "player.sqlite3",
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        node_path=tmp_path / "node.exe",
    )


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeController.instances = []
    FakeController.create_error = None
    FakeController.close_error = None
    FakeVoice.instances = []
    FakeVoice.mode = "running"
    FakeVoice.close_error = None
    monkeypatch.setattr(runtime_module, "DiscordVoice", FakeVoice)
    monkeypatch.setattr(runtime_module, "YouTubeResolver", FakeResolver)
    monkeypatch.setattr(runtime_module, "PlaybackController", FakeController)


def _messages(error: BaseException) -> tuple[str, ...]:
    if isinstance(error, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], error)
        return tuple(
            message for nested in group.exceptions for message in _messages(nested)
        )
    return (str(error),)


def test_runtime_uses_one_discord_client_task_on_the_current_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        async with open_runtime(_settings(tmp_path)) as yielded:
            voice = FakeVoice.instances[0]
            controller = FakeController.instances[0]
            assert voice.start_calls == 1
            assert voice.client_loop is loop
            assert voice.client_task is not None
            assert voice.client_task.get_name() == "discord-client"
            assert cast(object, yielded) is controller
            assert controller.restore_calls == 1
        assert controller.worker_closed
        assert voice.close_calls == 1
        assert voice.client_task is not None and voice.client_task.done()

    asyncio.run(scenario())


def test_runtime_wires_commands_to_the_shared_player_and_access_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    connected: list[int] = []

    async def connect(self: FakeController, channel_id: int) -> None:
        assert self is FakeController.instances[0]
        connected.append(channel_id)

    monkeypatch.setattr(FakeController, "connect", connect, raising=False)

    async def scenario() -> None:
        access = tmp_path / "custom-access.toml"
        async with open_runtime(_settings(tmp_path), access_path=access):
            voice = FakeVoice.instances[0]
            assert voice.command_access_path == access
            assert voice.command_connect is not None
            await voice.command_connect(7)
        assert connected == [7]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("authentication", "authentication failed"),
        ("ready", "gateway is unavailable"),
        ("early-exit", "stopped before startup"),
    ],
)
def test_client_startup_failures_clean_every_created_resource(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    expected: str,
) -> None:
    _install_fakes(monkeypatch)
    FakeVoice.mode = mode

    async def scenario() -> None:
        with pytest.raises(BaseException) as caught:
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")
        assert any(expected in message for message in _messages(caught.value))
        controller = FakeController.instances[0]
        voice = FakeVoice.instances[0]
        assert controller.worker_closed
        assert controller.close_calls == 1
        assert voice.close_calls == 1
        assert voice.client_task is not None and voice.client_task.done()

    asyncio.run(scenario())


def test_controller_startup_failure_closes_unstarted_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    FakeController.create_error = RuntimeError("database failed")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="database failed"):
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")
        voice = FakeVoice.instances[0]
        assert voice.start_calls == 0
        assert voice.close_calls == 1

    asyncio.run(scenario())


def test_cancelling_runtime_owner_cleans_controller_voice_and_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> None:
        entered = asyncio.Event()

        async def owner() -> None:
            async with open_runtime(_settings(tmp_path)):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        controller = FakeController.instances[0]
        voice = FakeVoice.instances[0]
        assert controller.worker_closed
        assert controller.close_calls == 1
        assert voice.close_calls == 1
        assert voice.client_task is not None and voice.client_task.done()

    asyncio.run(scenario())


def test_shutdown_errors_are_grouped_after_all_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fakes(monkeypatch)
    FakeController.close_error = RuntimeError("worker close failed")
    FakeVoice.close_error = VoiceError("voice close failed")

    async def scenario() -> None:
        with pytest.raises(ExceptionGroup) as caught:
            async with open_runtime(_settings(tmp_path)):
                pass
        messages = _messages(caught.value)
        assert "worker close failed" in messages
        assert "voice close failed" in messages
        controller = FakeController.instances[0]
        voice = FakeVoice.instances[0]
        assert controller.worker_closed
        assert controller.close_calls == 1
        assert voice.close_calls == 1
        assert voice.client_task is not None and voice.client_task.done()

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_client_exit_after_readiness_interrupts_owner_and_closes_every_resource(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fails: bool
) -> None:
    _install_fakes(monkeypatch)
    original_start = FakeVoice.start

    async def start_then_exit(self: FakeVoice, token: str) -> None:
        await original_start(self, token)
        if fails:
            raise VoiceError("Discord gateway failed.")

    monkeypatch.setattr(FakeVoice, "start", start_then_exit)

    async def scenario() -> None:
        async with asyncio.timeout(2):
            with pytest.raises(ExceptionGroup) as caught:
                async with open_runtime(_settings(tmp_path)):
                    voice = FakeVoice.instances[0]
                    voice.closed.set()
                    await asyncio.Event().wait()
            expected = "gateway failed" if fails else "stopped unexpectedly"
            assert any(expected in message for message in _messages(caught.value))
        assert FakeController.instances[0].worker_closed
        assert FakeVoice.instances[0].close_calls == 1
        client = FakeVoice.instances[0].client_task
        assert client is not None and client.done()

    asyncio.run(scenario())

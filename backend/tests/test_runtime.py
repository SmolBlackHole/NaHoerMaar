# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, cast
from unittest.mock import Mock

import pytest

import nahormaar_backend.runtime as runtime_module
from nahormaar_backend.application.audio import VoiceError
from nahormaar_backend.application.storage import PlayerStore
from nahormaar_backend.config import Settings
from nahormaar_backend.runtime import open_runtime


class Lifecycle:
    closed: ClassVar[list[str]] = []


class FakeResolver:
    def __init__(self, node_path: Path) -> None:
        self.node_path = node_path


class FakeCatalog:
    instances: ClassVar[list[FakeCatalog]] = []
    create_error: ClassVar[Exception | None] = None
    close_error: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.close_calls = 0
        type(self).instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1
        Lifecycle.closed.append("catalog")
        error = type(self).close_error
        if error is not None:
            raise error


async def _create_catalog(node_path: Path) -> FakeCatalog:
    del node_path
    if FakeCatalog.create_error is not None:
        raise FakeCatalog.create_error
    return FakeCatalog()


class FakeExtractor:
    instances: ClassVar[list[FakeExtractor]] = []

    def __init__(self, node_path: Path) -> None:
        del node_path
        self.close_calls = 0
        type(self).instances.append(self)

    async def execute(self, args: tuple[str, ...]) -> object:
        raise AssertionError("Runtime wiring must not execute discovery.")

    async def close(self) -> None:
        self.close_calls += 1
        Lifecycle.closed.append("radio extractor")


class FakeRadio:
    instances: ClassVar[list[FakeRadio]] = []
    close_error: ClassVar[Exception | None] = None

    def __init__(self, provider: object) -> None:
        del provider
        self.close_calls = 0
        type(self).instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1
        Lifecycle.closed.append("radio")
        error = type(self).close_error
        if error is not None:
            raise error


class FakeSession:
    instances: ClassVar[list[FakeSession]] = []
    close_error: ClassVar[Exception | None] = None

    def __init__(self, voice: FakeVoice) -> None:
        self.voice = voice
        self.close_calls = 0
        self.worker_closed = False
        self.restore_calls = 0
        self.connections: list[int] = []
        type(self).instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1
        self.worker_closed = True
        Lifecycle.closed.append("session")
        errors: list[Exception] = []
        error = type(self).close_error
        if error is not None:
            errors.append(error)
        try:
            await self.voice.close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise ExceptionGroup("Session shutdown failed.", errors)

    async def connect(self, channel_id: int) -> None:
        self.connections.append(channel_id)

    async def restore(self) -> None:
        assert FakeGateway.instances[0].started.is_set()
        self.restore_calls += 1


class FakeManager:
    instances: ClassVar[list[FakeManager]] = []
    create_error: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.session: FakeSession | None = None
        self.store_factory: Callable[[], PlayerStore] | None = None
        self.close_calls = 0
        type(self).instances.append(self)

    async def create(
        self,
        store_factory: Callable[[], PlayerStore],
        resolver: object,
        voice: FakeVoice,
        *,
        metadata_resolver: object,
        catalog: FakeCatalog,
        radio_catalog: FakeRadio,
    ) -> FakeSession:
        del resolver
        assert metadata_resolver is catalog
        assert catalog is FakeCatalog.instances[0]
        assert radio_catalog is FakeRadio.instances[0]
        self.store_factory = store_factory
        error = type(self).create_error
        if error is not None:
            await voice.close()
            raise error
        self.session = FakeSession(voice)
        return self.session

    async def close(self) -> None:
        self.close_calls += 1
        if self.session is not None:
            await self.session.close()


class FakeVoice:
    instances: ClassVar[list[FakeVoice]] = []
    close_error: ClassVar[Exception | None] = None

    def __init__(
        self,
        ffmpeg_path: Path,
        *,
        client: object,
        activity: Callable[..., None],
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.client = client
        self.activity = activity
        self.close_calls = 0
        self.validate_calls = 0
        type(self).instances.append(self)

    def validate_dependencies(self) -> None:
        self.validate_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        Lifecycle.closed.append("voice")
        error = type(self).close_error
        if error is not None:
            raise error


class FakeGateway:
    instances: ClassVar[list[FakeGateway]] = []
    mode: ClassVar[str] = "running"
    close_error: ClassVar[Exception | None] = None

    def __init__(self) -> None:
        self.client = object()
        self.voice: FakeVoice | None = None
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

    def set_activity(self, track: object = None, *, paused: bool = False) -> None:
        del track, paused

    def bind_voice(self, voice: FakeVoice) -> None:
        self.voice = voice

    def install_commands(
        self, access_path: Path, connect: Callable[[int], Awaitable[object]]
    ) -> None:
        assert self.start_calls == 0
        self.command_access_path = access_path
        self.command_connect = connect

    async def start(self, token: str, *, prepare: Callable[[], None]) -> None:
        prepare()
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
        Lifecycle.closed.append("gateway")
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


@pytest.fixture(autouse=True)
def runtime_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    Lifecycle.closed = []
    FakeSession.instances = []
    FakeSession.close_error = None
    FakeManager.instances = []
    FakeManager.create_error = None
    FakeVoice.instances = []
    FakeVoice.close_error = None
    FakeGateway.instances = []
    FakeGateway.mode = "running"
    FakeGateway.close_error = None
    FakeCatalog.instances = []
    FakeCatalog.create_error = None
    FakeCatalog.close_error = None
    FakeExtractor.instances = []
    FakeRadio.instances = []
    FakeRadio.close_error = None
    monkeypatch.setattr(runtime_module, "DiscordGateway", FakeGateway)
    monkeypatch.setattr(runtime_module, "DiscordVoice", FakeVoice)
    monkeypatch.setattr(runtime_module, "YouTubeResolver", FakeResolver)
    monkeypatch.setattr(runtime_module, "SessionManager", FakeManager)
    monkeypatch.setattr(runtime_module, "create_media_catalog", _create_catalog)
    monkeypatch.setattr(runtime_module, "DiscoveryExtractor", FakeExtractor)
    monkeypatch.setattr(runtime_module, "RadioCatalog", FakeRadio)


def _messages(error: BaseException) -> tuple[str, ...]:
    if isinstance(error, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], error)
        return tuple(
            message for nested in group.exceptions for message in _messages(nested)
        )
    return (str(error),)


def _assert_closed() -> None:
    assert FakeSession.instances[0].worker_closed
    assert FakeSession.instances[0].close_calls == 1
    assert FakeManager.instances[0].close_calls == 1
    assert FakeVoice.instances[0].close_calls == 1
    assert FakeCatalog.instances[0].close_calls == 1
    assert FakeRadio.instances[0].close_calls == 1
    assert FakeExtractor.instances[0].close_calls == 1
    gateway = FakeGateway.instances[0]
    assert gateway.close_calls == 1
    assert gateway.client_task is not None and gateway.client_task.done()
    assert Lifecycle.closed == [
        "session",
        "voice",
        "radio",
        "radio extractor",
        "catalog",
        "gateway",
    ]


def test_runtime_uses_one_discord_client_task_on_the_current_loop(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        async with open_runtime(_settings(tmp_path)) as yielded:
            gateway = FakeGateway.instances[0]
            session = FakeSession.instances[0]
            voice = FakeVoice.instances[0]
            assert gateway.start_calls == 1
            assert gateway.client_loop is loop
            assert gateway.client_task is not None
            assert gateway.client_task.get_name() == "discord-client"
            assert cast(object, yielded.session) is session
            assert cast(object, yielded.catalog) is FakeCatalog.instances[0]
            assert cast(object, yielded.radio_catalog) is FakeRadio.instances[0]
            assert gateway.voice is voice
            assert voice.client is gateway.client
            assert voice.activity == gateway.set_activity
            assert voice.validate_calls == 1
            assert session.restore_calls == 1
        _assert_closed()

    asyncio.run(scenario())


def test_runtime_wires_commands_to_the_shared_session_and_access_file(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        access = tmp_path / "custom-access.toml"
        async with open_runtime(_settings(tmp_path), access_path=access):
            gateway = FakeGateway.instances[0]
            assert gateway.command_access_path == access
            assert gateway.command_connect is not None
            await gateway.command_connect(7)
        assert FakeSession.instances[0].connections == [7]

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
    tmp_path: Path,
    mode: str,
    expected: str,
) -> None:
    FakeGateway.mode = mode

    async def scenario() -> None:
        with pytest.raises(BaseException) as caught:
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")
        assert any(expected in message for message in _messages(caught.value))
        _assert_closed()

    asyncio.run(scenario())


def test_session_startup_failure_does_not_close_transferred_voice_twice(
    tmp_path: Path,
) -> None:
    FakeManager.create_error = RuntimeError("database failed")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="database failed"):
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")
        assert FakeGateway.instances[0].start_calls == 0
        assert FakeGateway.instances[0].close_calls == 1
        assert FakeVoice.instances[0].close_calls == 1
        assert FakeManager.instances[0].close_calls == 1
        assert FakeCatalog.instances[0].close_calls == 1
        assert FakeRadio.instances[0].close_calls == 1
        assert FakeExtractor.instances[0].close_calls == 1

    asyncio.run(scenario())


def test_catalog_startup_failure_closes_only_created_resources(tmp_path: Path) -> None:
    FakeCatalog.create_error = RuntimeError("catalog failed")

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="catalog failed"):
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")
        assert FakeGateway.instances[0].start_calls == 0
        assert FakeGateway.instances[0].close_calls == 1
        assert FakeVoice.instances[0].close_calls == 1
        assert FakeManager.instances == []
        assert FakeCatalog.instances == []
        assert FakeRadio.instances == []
        assert FakeExtractor.instances == []
        assert Lifecycle.closed == ["voice", "gateway"]

    asyncio.run(scenario())


def test_cancelling_during_catalog_creation_reaps_untransferred_voice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def pending_catalog(node_path: Path) -> FakeCatalog:
            del node_path
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(runtime_module, "create_media_catalog", pending_catalog)

        async def owner() -> None:
            async with open_runtime(_settings(tmp_path)):
                raise AssertionError("runtime must not yield")

        task = asyncio.create_task(owner())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert FakeVoice.instances[0].close_calls == 1
        assert FakeGateway.instances[0].close_calls == 1

    asyncio.run(scenario())


def test_cancelling_runtime_owner_cleans_session_voice_and_gateway(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def owner() -> None:
            async with open_runtime(_settings(tmp_path)):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        _assert_closed()

    asyncio.run(scenario())


def test_shutdown_errors_are_grouped_after_all_cleanup_finishes(tmp_path: Path) -> None:
    FakeSession.close_error = RuntimeError("worker close failed")
    FakeVoice.close_error = VoiceError("voice close failed")
    FakeCatalog.close_error = RuntimeError("catalog close failed")
    FakeRadio.close_error = RuntimeError("radio close failed")
    FakeGateway.close_error = VoiceError("gateway close failed")

    async def scenario() -> None:
        with pytest.raises(ExceptionGroup) as caught:
            async with open_runtime(_settings(tmp_path)):
                pass
        messages = _messages(caught.value)
        for expected in (
            "worker close failed",
            "voice close failed",
            "catalog close failed",
            "radio close failed",
            "gateway close failed",
        ):
            assert expected in messages
        _assert_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_client_exit_after_readiness_interrupts_owner_and_closes_every_resource(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fails: bool,
) -> None:
    original_start = FakeGateway.start

    async def start_then_exit(
        self: FakeGateway,
        token: str,
        *,
        prepare: Callable[[], None],
    ) -> None:
        await original_start(self, token, prepare=prepare)
        if fails:
            raise VoiceError("Discord gateway failed.")

    monkeypatch.setattr(FakeGateway, "start", start_then_exit)

    async def scenario() -> None:
        async with asyncio.timeout(2):
            with pytest.raises(ExceptionGroup) as caught:
                async with open_runtime(_settings(tmp_path)):
                    FakeGateway.instances[0].closed.set()
                    await asyncio.Event().wait()
            expected = "gateway failed" if fails else "stopped unexpectedly"
            assert any(expected in message for message in _messages(caught.value))
        _assert_closed()

    asyncio.run(scenario())


def test_storage_factory_defers_directory_and_database_creation_to_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "new-directory")
    creation_threads: list[int] = []
    storage = Mock(spec=PlayerStore)

    def create_storage(path: Path) -> object:
        assert path == settings.database_path
        assert path.parent.is_dir()
        creation_threads.append(threading.get_ident())
        return storage

    monkeypatch.setattr(runtime_module, "SQLiteStore", create_storage)

    async def scenario() -> None:
        owner_thread = threading.get_ident()
        async with open_runtime(settings):
            assert not settings.database_path.parent.exists()
            factory = FakeManager.instances[0].store_factory
            assert factory is not None
            assert await asyncio.to_thread(factory) is storage
        assert len(creation_threads) == 1
        assert creation_threads[0] != owner_thread

    asyncio.run(scenario())

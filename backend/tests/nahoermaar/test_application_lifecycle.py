# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from nahoermaar import bootstrap as bootstrap_module
from nahoermaar.bootstrap import Application, ApplicationLifecycle
from nahoermaar.catalog.service import CatalogService
from nahoermaar.config import Settings
from nahoermaar.database.core import Database
from nahoermaar.listening.service import ListeningService
from nahoermaar.messaging import Command, MessageBus, MessageContext
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.player.domain import ListeningSessionId
from nahoermaar.player.playback import PlaybackCoordinator
from nahoermaar.player.session import PlayerSessionManager
from nahoermaar.statistics.service import StatisticsService
from nahoermaar.views.profile import ProfileView
from nahoermaar.views.recent import RecentListeningView
from nahoermaar.users.service import AccessService, AuthService
from nahoermaar.integrations.discord import DiscordGateway


class _Bus(MessageBus):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def execute[ResultT](
        self,
        command: Command[ResultT],
        context: MessageContext | None = None,
    ) -> ResultT:
        _ = command, context
        self._calls.append("operators")
        return cast(ResultT, None)


class _Database:
    engine = object()

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def close(self) -> None:
        self._calls.append("database.close")


class _Catalog:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def close(self) -> None:
        self._calls.append("catalog.close")


class _Player:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self.state = SimpleNamespace(
            session=SimpleNamespace(id=ListeningSessionId(uuid4()))
        )

    async def start(self) -> None:
        self._calls.append("player.start")

    async def close(self) -> None:
        self._calls.append("player.close")


class _Listening:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self, session_id: ListeningSessionId) -> None:
        assert session_id
        self._calls.append("listening.start")

    async def close(self) -> None:
        self._calls.append("listening.close")


class _Gateway:
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self._calls = calls
        self._fail = fail

    async def open(self) -> None:
        self._calls.append("gateway.open")
        if self._fail:
            raise RuntimeError("gateway failed")

    async def close(self) -> None:
        self._calls.append("gateway.close")


class _Playback:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("playback.start")

    async def close(self) -> None:
        self._calls.append("playback.close")


def _application(calls: list[str], *, fail_gateway: bool = False) -> Application:
    database = _Database(calls)
    catalog = _Catalog(calls)
    player = _Player(calls)
    listening = _Listening(calls)
    gateway = _Gateway(calls, fail=fail_gateway)
    playback = _Playback(calls)
    return Application(
        cast(Settings, object()),
        cast(Database, database),
        _Bus(calls),
        cast(AuthService, object()),
        cast(AccessService, object()),
        cast(CatalogService, catalog),
        cast(PlayerSessionManager, player),
        cast(ListeningService, listening),
        cast(StatisticsService, object()),
        cast(ProfileView, object()),
        cast(RecentListeningView, object()),
        RecentLogBuffer(),
        cast(DiscordGateway, gateway),
        cast(PlaybackCoordinator, playback),
    )


def test_failed_start_closes_started_resources_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def migrate(_engine: object) -> None:
        calls.append("migrate")

    monkeypatch.setattr(bootstrap_module, "migrate", migrate)
    application = _application(calls, fail_gateway=True)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="gateway failed"):
            await application.start()
        assert application.lifecycle is ApplicationLifecycle.CLOSED
        assert calls == [
            "migrate",
            "operators",
            "player.start",
            "listening.start",
            "gateway.open",
            "gateway.close",
            "listening.close",
            "player.close",
            "catalog.close",
            "database.close",
        ]
        await application.close()

    asyncio.run(scenario())


def test_application_shutdown_is_reverse_ordered_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def migrate(_engine: object) -> None:
        calls.append("migrate")

    monkeypatch.setattr(bootstrap_module, "migrate", migrate)
    application = _application(calls)

    async def scenario() -> None:
        await application.start()
        await application.close()
        assert application.lifecycle is ApplicationLifecycle.CLOSED
        await application.close()

    asyncio.run(scenario())
    assert calls == [
        "migrate",
        "operators",
        "player.start",
        "listening.start",
        "gateway.open",
        "playback.start",
        "playback.close",
        "gateway.close",
        "listening.close",
        "player.close",
        "catalog.close",
        "database.close",
    ]

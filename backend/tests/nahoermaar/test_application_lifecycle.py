# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from nahoermaar import bootstrap as bootstrap_module
from nahoermaar.bootstrap import Application, ApplicationLifecycle
from nahoermaar.catalog.main import CatalogModule
from nahoermaar.catalog.service import CatalogService
from nahoermaar.config import Settings
from nahoermaar.database.core import Database
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.library.main import LibraryModule
from nahoermaar.library.service import LibraryService
from nahoermaar.listening.main import ListeningModule
from nahoermaar.listening.service import ListeningService
from nahoermaar.lyrics.main import LyricsModule
from nahoermaar.lyrics.providers import LyricsProvider
from nahoermaar.lyrics.service import LyricsService
from nahoermaar.messaging import Command, MessageBus, MessageContext
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.operations.incidents import IncidentService
from nahoermaar.operations.jobs import JobRunService
from nahoermaar.operations.maintenance import HousekeepingContribution
from nahoermaar.operations.main import OperationsModule
from nahoermaar.operations.scheduler import JobCoordinator
from nahoermaar.player.automation import PlaybackAutomation
from nahoermaar.player.domain import ListeningSessionId
from nahoermaar.player.main import PlayerModule, SummonHandler
from nahoermaar.player.playback import PlaybackCoordinator
from nahoermaar.player.read_model import PlayerReader
from nahoermaar.player.session import PlayerSessionManager
from nahoermaar.statistics.main import StatisticsModule
from nahoermaar.statistics.service import StatisticsService
from nahoermaar.views.main import ViewsModule
from nahoermaar.users.service import AccessService, AuthService, IdentityProvider
from nahoermaar.users.main import UsersModule
from nahoermaar.integrations.discord import DiscordGateway
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.integrations.main import IntegrationsModule


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

    async def start(self) -> None:
        self._calls.append("catalog.start")

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


class _Automation:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("automation.start")

    async def close(self) -> None:
        self._calls.append("automation.close")


class _Jobs:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def reconcile_interrupted_runs(self) -> None:
        self._calls.append("job_runs.reconcile")


class _JobCoordinator:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("jobs.start")

    async def close(self) -> None:
        self._calls.append("jobs.close")


class _Playback:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def start(self) -> None:
        self._calls.append("playback.start")

    async def close(self) -> None:
        self._calls.append("playback.close")


def _application(
    calls: list[str],
    *,
    fail_gateway: bool = False,
    catalog_resource_name: str = "catalog",
) -> Application:
    database = _Database(calls)
    catalog = _Catalog(calls)
    player = _Player(calls)
    listening = _Listening(calls)
    gateway = _Gateway(calls, fail=fail_gateway)
    playback = _Playback(calls)
    automation = _Automation(calls)
    jobs = _Jobs(calls)
    coordinator = _JobCoordinator(calls)

    async def reconcile_operators() -> None:
        calls.append("operators")

    users = UsersModule(
        cast(AuthService, object()),
        cast(AccessService, object()),
        cast(HousekeepingContribution, object()),
        LifecycleResource("users.operators", start=reconcile_operators),
    )

    async def start_listening() -> None:
        await listening.start(player.state.session.id)

    listening_module = ListeningModule(
        cast(ListeningService, listening),
        LifecycleResource(
            "listening",
            start=start_listening,
            close=listening.close,
        ),
    )
    player_module = PlayerModule(
        service=cast(PlayerSessionManager, player),
        reader=cast(PlayerReader, object()),
        automation=cast(PlaybackAutomation, automation),
        playback=cast(PlaybackCoordinator, playback),
        housekeeping=cast(HousekeepingContribution, object()),
        summon=cast(SummonHandler, object()),
        session_lifecycle=LifecycleResource(
            "player",
            start=player.start,
            close=player.close,
        ),
        automation_lifecycle=LifecycleResource(
            "automation",
            start=automation.start,
            close=automation.close,
        ),
        playback_lifecycle=LifecycleResource(
            "playback",
            start=playback.start,
            close=playback.close,
        ),
    )
    integrations = IntegrationsModule(
        identity=cast(IdentityProvider, object()),
        catalog_providers=(),
        lyrics_provider=cast(LyricsProvider, object()),
        avatars=DiscordAvatarStore(Path("data/avatars")),
        housekeeping=cast(HousekeepingContribution, object()),
        gateway=cast(DiscordGateway, gateway),
        playback_transport=None,
        gateway_lifecycle=LifecycleResource(
            "discord",
            start=gateway.open,
            close=gateway.close,
        ),
    )
    operations = OperationsModule(
        incidents=cast(IncidentService, object()),
        job_runs=cast(JobRunService, jobs),
        jobs=cast(JobCoordinator, coordinator),
        logs=RecentLogBuffer(),
        reconciliation_lifecycle=LifecycleResource(
            "job-runs.reconcile",
            start=jobs.reconcile_interrupted_runs,
        ),
        jobs_lifecycle=LifecycleResource(
            "jobs",
            start=coordinator.start,
            close=coordinator.close,
        ),
    )
    return Application(
        cast(Settings, object()),
        cast(Database, database),
        _Bus(calls),
        users,
        CatalogModule(
            cast(CatalogService, catalog),
            (),
            cast(HousekeepingContribution, object()),
            LifecycleResource(catalog_resource_name, close=catalog.close),
        ),
        LibraryModule(
            cast(LibraryService, object()),
            (),
            cast(HousekeepingContribution, object()),
        ),
        LyricsModule(cast(LyricsService, object())),
        player_module,
        listening_module,
        StatisticsModule(cast(StatisticsService, object())),
        cast(ViewsModule, object()),
        integrations,
        operations,
    )


def test_application_rejects_duplicate_lifecycle_owners() -> None:
    with pytest.raises(ValueError, match="resource names must be unique"):
        _application([], catalog_resource_name="database")


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
            "job_runs.reconcile",
            "operators",
            "player.start",
            "listening.start",
            "automation.start",
            "jobs.start",
            "gateway.open",
            "gateway.close",
            "jobs.close",
            "automation.close",
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
        "job_runs.reconcile",
        "operators",
        "player.start",
        "listening.start",
        "automation.start",
        "jobs.start",
        "gateway.open",
        "playback.start",
        "playback.close",
        "gateway.close",
        "jobs.close",
        "automation.close",
        "listening.close",
        "player.close",
        "catalog.close",
        "database.close",
    ]

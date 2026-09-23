# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

# pyright: reportPrivateUsage=false
import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import discord
import pytest
import httpx
from alembic.autogenerate import compare_metadata
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import nahormaar_backend.engine.bootstrap as bootstrap
import nahormaar_backend.engine.gateway as gateway_module
import nahormaar_backend.config as config_module
import nahormaar_backend.__main__ as entrypoint
from nahormaar_backend.config import AuthSettings, Settings
from nahormaar_backend.engine.api import create_app
from nahormaar_backend.engine.commands import DiscordCommands
from nahormaar_backend.engine.discord import DiscordOutput
from nahormaar_backend.engine.domain.playback import Control, Join
from nahormaar_backend.engine.domain.queue import Add
from nahormaar_backend.engine.domain.sessions import ListeningSession, PlaybackPhase
from nahormaar_backend.engine.persistence import (
    ListeningSessionRepository,
    database_engine,
    write_transaction,
)
from nahormaar_backend.engine.schema import initialize, metadata, REVISION
from .database import database_url
from .test_engine_api import Provider
from .test_playback_session import ControlledOutput, ControlledVoice
from .test_radio_session import until


def test_initialize_empty_database_reopens_one_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "new" / "engine.db"
        first = await initialize(database_url(path))
        engine = database_engine(database_url(path))
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.run_sync(
                        lambda conn: MigrationContext.configure(
                            conn
                        ).get_current_revision()
                    )
                    == REVISION
                )
                assert not await connection.run_sync(
                    lambda conn: compare_metadata(
                        MigrationContext.configure(conn), metadata()
                    )
                )
            sessions = async_sessionmaker(
                engine, expire_on_commit=False, autobegin=False
            )
            async with write_transaction(sessions) as db:
                repository = ListeningSessionRepository(db)
                settings = await repository.get(first)
                assert settings
                await repository.update(replace(settings, volume=0.5))
            assert await initialize(database_url(path)) == first
            async with sessions.begin() as db:
                restored = await ListeningSessionRepository(db).get(first)
                assert restored and restored.volume == 0.5
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("revision", ["engine_0001", "engine_0002"])
def test_initialize_upgrades_previous_engine_revision_without_losing_session(
    tmp_path: Path,
    revision: str,
) -> None:
    path = tmp_path / "previous.db"
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url(path).replace("%", "%%"))
    command.upgrade(config, revision)
    original = ListeningSession(channel_id=123, volume=0.4)

    async def scenario() -> None:
        engine = database_engine(database_url(path))
        try:
            sessions = async_sessionmaker(
                engine, expire_on_commit=False, autobegin=False
            )
            async with write_transaction(sessions) as db:
                await ListeningSessionRepository(db).add(original)
        finally:
            await engine.dispose()

        assert await initialize(database_url(path)) == original.id
        engine = database_engine(database_url(path))
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.run_sync(
                        lambda conn: MigrationContext.configure(
                            conn
                        ).get_current_revision()
                    )
                    == REVISION
                )
                assert not await connection.run_sync(
                    lambda conn: compare_metadata(
                        MigrationContext.configure(conn), metadata()
                    )
                )
            sessions = async_sessionmaker(
                engine, expire_on_commit=False, autobegin=False
            )
            async with sessions.begin() as db:
                assert await ListeningSessionRepository(db).get(original.id) == original
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_alembic_cli_targets_engine_schema_and_rejects_old_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cli.db"
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url(path).replace("%", "%%"))
    command.upgrade(config, "head")
    command.check(config)

    async def scenario() -> None:
        await initialize(database_url(path))
        engine = database_engine(database_url(path))
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE alembic_version SET version_num='0011'")
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    with pytest.raises(CommandError, match="0011"):
        command.upgrade(config, "head")

    async def assert_revision_unchanged() -> None:
        engine = database_engine(database_url(path))
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.run_sync(
                        lambda conn: MigrationContext.configure(
                            conn
                        ).get_current_revision()
                    )
                    == "0011"
                )
        finally:
            await engine.dispose()

    asyncio.run(assert_revision_unchanged())


@pytest.mark.parametrize("kind", ["legacy", "foreign", "multiple"])
def test_initialize_rejects_existing_data_without_changes(
    tmp_path: Path, kind: str
) -> None:
    async def scenario() -> None:
        path = tmp_path / "existing.db"
        if kind == "legacy":
            engine = database_engine(database_url(path))
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
                    )
                    await connection.execute(
                        text("INSERT INTO alembic_version VALUES ('0011')")
                    )
                    await connection.execute(
                        text("CREATE TABLE queue_entries (title TEXT)")
                    )
                    await connection.execute(
                        text("INSERT INTO queue_entries VALUES ('Keep this track')")
                    )
            finally:
                await engine.dispose()
        else:
            if kind == "multiple":
                await initialize(database_url(path))
            engine = database_engine(database_url(path))
            try:
                if kind == "foreign":
                    async with engine.begin() as connection:
                        await connection.execute(
                            text("CREATE VIEW unrelated AS SELECT 1 AS value")
                        )
                else:
                    sessions = async_sessionmaker(
                        engine, expire_on_commit=False, autobegin=False
                    )
                    async with write_transaction(sessions) as db:
                        await ListeningSessionRepository(db).add(ListeningSession())
            finally:
                await engine.dispose()
        with pytest.raises(
            ValueError, match=r"fresh DATABASE_URL|one listening session"
        ):
            await initialize(database_url(path))
        engine = database_engine(database_url(path))
        try:
            async with engine.connect() as connection:
                if kind == "legacy":
                    assert (
                        await connection.scalar(text("SELECT title FROM queue_entries"))
                        == "Keep this track"
                    )
                elif kind == "foreign":
                    assert "unrelated" in await connection.run_sync(
                        lambda conn: inspect(conn).get_view_names()
                    )
                else:
                    assert (
                        await connection.scalar(
                            text("SELECT COUNT(*) FROM listening_sessions")
                        )
                        == 2
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_initialize_rolls_back_schema_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def reject(self: ListeningSessionRepository, value: ListeningSession) -> None:
        raise RuntimeError("fixture write failed")

    async def scenario() -> None:
        path = tmp_path / "first.db"
        with monkeypatch.context() as patch:
            patch.setattr(ListeningSessionRepository, "add", reject)
            with pytest.raises(RuntimeError, match="fixture write failed"):
                await initialize(database_url(path))
        engine = database_engine(database_url(path))
        try:
            async with engine.connect() as connection:
                assert not await connection.run_sync(
                    lambda conn: inspect(conn).get_table_names()
                )
        finally:
            await engine.dispose()
        assert isinstance(await initialize(database_url(path)), UUID)

    asyncio.run(scenario())


class Output(ControlledOutput, ControlledVoice):
    def __init__(self) -> None:
        ControlledOutput.__init__(self)
        ControlledVoice.__init__(self)
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1
        await ControlledOutput.close(self)
        await ControlledVoice.close(self)


class Gateway(gateway_module.DiscordGateway):
    def __init__(self, ffmpeg_path: Path) -> None:
        super().__init__(ffmpeg_path)
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.ready_allowed = asyncio.Event()
        self.ready_allowed.set()
        self.failure = False
        self.closed = 0
        self.output_closed_at_close = 0

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.started.set()
        # Real Client.__aenter__ must run before readiness is used.
        assert isinstance(self._ready, asyncio.Event)
        if self.failure:
            raise RuntimeError("fixture gateway failure")
        await self.ready_allowed.wait()
        self._ready.set()
        await self.finish.wait()

    async def close(self) -> None:
        if isinstance(self.output, Output):
            self.output_closed_at_close = self.output.closed
        self.closed += 1
        await super().close()


@asynccontextmanager
async def runtime_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "ready",
) -> AsyncGenerator[
    tuple[
        Settings, AuthSettings, list[Gateway], list[Output], list[Provider], list[str]
    ]
]:
    gateways: list[Gateway] = []
    outputs: list[Output] = []
    providers: list[Provider] = []
    calls: list[str] = []

    def gateway_factory(path: Path) -> Gateway:
        gateway = Gateway(path)
        gateway.failure = mode == "failure"
        if mode in {"waiting", "timeout"}:
            gateway.ready_allowed.clear()
        gateways.append(gateway)
        return gateway

    def output_factory(client: discord.Client, path: Path) -> Output:
        output = Output()
        outputs.append(output)
        return output

    def provider_factory(path: Path) -> Provider:
        provider = Provider()
        if len(providers) % 2:
            provider.key = "youtube"
        providers.append(provider)
        return provider

    async def register(self: DiscordCommands) -> None:
        assert gateways[-1].is_ready()
        calls.append("register")
        if mode == "registration":
            raise RuntimeError("fixture registration failure")

    async def profile(self: Gateway, *args: object) -> None:
        calls.append("profile")
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("profile_closed")

    async def bio(client: discord.Client) -> None:
        calls.append("bio")
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("bio_closed")

    with monkeypatch.context() as patch:
        patch.setattr(DiscordOutput, "validate_dependencies", lambda: None)
        patch.setattr(gateway_module, "DiscordOutput", output_factory)
        patch.setattr(bootstrap, "DiscordGateway", gateway_factory)
        patch.setattr(bootstrap, "YouTubeMusicProvider", provider_factory)
        patch.setattr(bootstrap, "YouTubeProvider", provider_factory)
        patch.setattr(DiscordCommands, "register", register)
        patch.setattr(Gateway, "update_presence", profile)
        patch.setattr(bootstrap, "update_daily_bio", bio)
        if mode == "timeout":
            patch.setattr(bootstrap, "_READY_TIMEOUT_SECONDS", 0.03)
        path = tmp_path / "engine.db"
        settings = Settings(
            "fixture-only",
            database_url(path),
            Path("unused-ffmpeg"),
            Path("unused-node"),
        )
        auth = AuthSettings(
            "http://localhost",
            "",
            "",
            database_url(path),
            tmp_path / "access.toml",
        )
        yield settings, auth, gateways, outputs, providers, calls
        assert all(gateway.closed == 1 for gateway in gateways)
        assert all(provider.closed for provider in providers)
        assert not [
            task
            for task in asyncio.all_tasks()
            if task.get_name().startswith("engine-")
        ]


@pytest.mark.parametrize("paused", [False, True])
def test_runtime_restarts_same_session_play_and_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paused: bool
) -> None:
    async def scenario() -> None:
        async with runtime_fixture(tmp_path, monkeypatch) as (
            settings,
            auth,
            gateways,
            outputs,
            providers,
            calls,
        ):
            async with bootstrap.open_runtime(settings, auth) as services:
                track = await services.catalog.track(
                    providers[0].finding.reference.source_url
                )
                await services.session.request(uuid4(), Add((track.id,)))
                await services.session.request(uuid4(), Join(123))
                await until(lambda: outputs[0].progress is not None)
                outputs[0].confirm(35)
                await until(lambda: len(services.session.snapshot.history) == 1)
                if paused:
                    await services.session.request(uuid4(), Control.PAUSE)
                session_id = services.session.snapshot.settings.id
                play_id = services.session.snapshot.checkpoint.play_id
            assert outputs[0].closed == 1 and outputs[0].connection is None
            assert gateways[0].output_closed_at_close == 1
            assert "profile_closed" in calls and "bio_closed" in calls
            async with bootstrap.open_runtime(settings, auth) as services:
                await until(lambda: outputs[1].progress is not None)
                assert outputs[1].progress
                assert outputs[1].progress.position_seconds == 35
                assert outputs[1].progress.paused is paused
                outputs[1].confirm(35)
                await until(
                    lambda: (
                        services.session.snapshot.playback.phase
                        in {PlaybackPhase.PLAYING, PlaybackPhase.PAUSED}
                    )
                )
                snapshot = services.session.snapshot
                assert snapshot.settings.id == session_id
                assert snapshot.checkpoint.play_id == play_id
                assert len(snapshot.history) == 1 and not snapshot.queue.entries
            assert outputs[1].closed == 1 and len(gateways) == 2
            assert calls.count("register") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode", ["failure", "timeout", "waiting", "exited", "registration"]
)
def test_runtime_startup_failure_cancellation_and_gateway_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def scenario() -> None:
        async with runtime_fixture(tmp_path, monkeypatch, mode=mode) as (
            settings,
            auth,
            gateways,
            outputs,
            providers,
            calls,
        ):

            async def run() -> None:
                async with bootstrap.open_runtime(settings, auth):
                    if mode == "exited":
                        gateways[0].finish.set()
                        await asyncio.Event().wait()
                    assert mode == "registration"

            if mode == "waiting":
                task = asyncio.create_task(run())
                await until(lambda: bool(gateways) and gateways[0].started.is_set())
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif mode == "registration":
                await run()
                assert "register" in calls
            else:
                with pytest.raises(ExceptionGroup) as failure:
                    await run()
                if mode == "failure":
                    assert isinstance(failure.value.exceptions[0], RuntimeError)
                    assert "fixture gateway failure" in str(failure.value.exceptions[0])
                elif mode == "timeout":
                    assert isinstance(failure.value.exceptions[0], TimeoutError)
                else:
                    assert "stopped unexpectedly" in str(failure.value.exceptions[0])
            assert all(
                output.progress is None and output.connection is None
                for output in outputs
            )
            if mode in {"failure", "timeout", "waiting"}:
                assert not providers and not calls
            else:
                assert outputs[0].closed == 1

    asyncio.run(scenario())


def test_profile_uses_committed_metadata_and_retries_without_audio_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_engine_api import fixture

    async def scenario() -> None:
        async with fixture(tmp_path) as (_http, services, provider, audio):
            async with Gateway(Path("unused")) as gateway:
                assert isinstance(gateway._ready, asyncio.Event)
                gateway._ready.set()
                sent: list[discord.Activity | discord.CustomActivity] = []
                attempts = 0

                async def change_presence(*, activity: discord.BaseActivity) -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("fixture presence failure")
                    assert isinstance(
                        activity, (discord.Activity, discord.CustomActivity)
                    )
                    sent.append(activity)

                monkeypatch.setattr(gateway, "change_presence", change_presence)
                monkeypatch.setattr(gateway_module, "_PRESENCE_INTERVAL_SECONDS", 0.01)
                task = asyncio.create_task(
                    gateway.update_presence(services.session, services.metadata)
                )
                try:
                    await until(lambda: bool(sent))
                    assert sent[-1].name == "Bereit für Musik"
                    assert attempts == 2
                    track = await services.catalog.track(
                        provider.finding.reference.source_url
                    )
                    await services.session.request(uuid4(), Add((track.id,)))
                    await services.session.request(uuid4(), Join(123))
                    await until(lambda: audio.progress is not None)
                    audio.confirm(1)
                    await until(lambda: sent[-1].name == "Амура")
                    await services.session.request(uuid4(), Control.PAUSE)
                    await until(lambda: sent[-1].name == "Pausiert: Амура")
                    before = len(sent)
                    await gateway.on_ready()
                    await until(lambda: len(sent) > before)
                    assert services.session.snapshot.checkpoint.track_id == track.id
                    assert audio.progress and audio.progress.paused
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_normal_application_factory_is_lazy_and_uses_new_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ffmpeg(override: str | None = None) -> Path:
        return Path("unused")

    def version(executable: Path, option: str = "--version") -> str:
        return "v24.11.0"

    async def scenario() -> None:
        async with runtime_fixture(tmp_path, monkeypatch) as (_, _, gateways, _, _, _):
            monkeypatch.chdir(tmp_path)
            monkeypatch.setattr(config_module, "ffmpeg_executable", ffmpeg)
            monkeypatch.setattr(config_module, "executable_version", version)
            path = tmp_path / "custom.db"
            values = {
                "DISCORD_TOKEN": "fixture-only",
                "DATABASE_URL": database_url(path),
                "NODE_PATH": sys.executable,
            }
            shutdown = asyncio.Event()
            app = bootstrap.create_application(environ=values, shutdown_event=shutdown)
            assert not gateways
            routes = app.openapi()["paths"]
            assert "/api/session" in routes and "/api/catalog/search" in routes
            assert "/api/state" not in routes
            async with app.router.lifespan_context(app):
                assert gateways[0].is_ready()
                engine = database_engine(values["DATABASE_URL"])
                try:
                    async with engine.connect() as connection:
                        assert "listening_sessions" in await connection.run_sync(
                            lambda conn: inspect(conn).get_table_names()
                        )
                finally:
                    await engine.dispose()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://localhost"
                ) as client:
                    assert (await client.get("/api/session")).status_code == 401
                shutdown.set()

    asyncio.run(scenario())


def test_entrypoint_rejects_occupied_port_before_runtime_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    import uvicorn

    def occupied(config: uvicorn.Config) -> None:
        raise SystemExit(1)

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("The bot must not start when its API port is already occupied.")

    def application(*, shutdown_event: asyncio.Event) -> FastAPI:
        return FastAPI()

    monkeypatch.setattr(entrypoint, "create_application", application)
    monkeypatch.setattr(uvicorn.Config, "bind_socket", occupied)
    monkeypatch.setattr(entrypoint.LocalServer, "run", unexpected_run)
    with pytest.raises(SystemExit) as failure:
        entrypoint.main()
    assert failure.value.code == 1


def test_gateway_exit_also_stops_http_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    async def scenario() -> None:
        async with runtime_fixture(tmp_path, monkeypatch) as (
            settings,
            auth,
            gateways,
            _,
            _,
            _,
        ):
            shutdown = asyncio.Event()
            app = create_app(
                lambda: bootstrap.open_runtime(settings, auth),
                public_origin=auth.public_origin,
                shutdown_event=shutdown,
            )
            with pytest.raises(ExceptionGroup):
                async with app.router.lifespan_context(app):
                    gateways[0].finish.set()
                    await asyncio.Event().wait()
            assert shutdown.is_set()
            server = entrypoint.LocalServer(uvicorn.Config(app), shutdown)
            assert await server.on_tick(0)

    asyncio.run(scenario())

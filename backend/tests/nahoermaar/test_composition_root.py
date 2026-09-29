# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Connection, inspect
from sqlalchemy.exc import SQLAlchemyError

from nahoermaar.api.app import create_app
from nahoermaar.bootstrap import bootstrap
from nahoermaar.catalog.service import CatalogService
from nahoermaar.library.service import LibraryService
from nahoermaar.config import LogLevel
from nahoermaar.database.core import Database
from nahoermaar.messaging import MessageBus
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.operations.jobs import JobId
from nahoermaar.player.main import PlayerModule
from nahoermaar.player.session import PlayerSessionManager
from nahoermaar.statistics.service import StatisticsService
from nahoermaar.users.domain import AccessRole
from nahoermaar.users.repository import UserRepository
from nahoermaar.views.catalog import CatalogCleanupView
from nahoermaar.views.history import PlaybackHistoryView
from nahoermaar.views.profile import ProfileView


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def test_bootstrap_loads_settings_and_composes_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured: list[tuple[LogLevel, Path, int]] = []

    def configure(level: LogLevel, directory: Path, retention: int) -> RecentLogBuffer:
        configured.append((level, directory, retention))
        return RecentLogBuffer()

    monkeypatch.setattr("nahoermaar.bootstrap.configure_logging", configure)
    access_path = tmp_path / "access.toml"
    access_path.write_text('owner_id = "9"\nadmin_ids = ["8"]\n', encoding="utf-8")

    application = bootstrap(
        {
            "DATABASE_URL": "postgresql+psycopg://app:secret@database/nahoermaar",
            "LOG_LEVEL": "WARNING",
            "ACCESS_PATH": str(access_path),
        }
    )

    assert application.settings.log_level is LogLevel.WARNING
    assert application.users.access.operators.owner_id == "9"
    assert isinstance(application.bus, MessageBus)
    assert isinstance(application.statistics.service, StatisticsService)
    assert isinstance(application.views.profiles, ProfileView)
    assert isinstance(application.views.history, PlaybackHistoryView)
    assert isinstance(application.views.catalog_cleanup, CatalogCleanupView)
    assert isinstance(application.catalog.service, CatalogService)
    assert isinstance(application.library.service, LibraryService)
    assert application.catalog.lifecycle.name == "catalog"
    assert application.catalog.lifecycle.start is None
    assert isinstance(application.player, PlayerModule)
    assert isinstance(application.player.service, PlayerSessionManager)
    assert application.player.session_lifecycle.name == "player"
    assert application.player.automation_lifecycle.name == "automation"
    assert application.player.playback is None
    assert application.player.playback_lifecycle is None
    assert application.integrations.gateway is None
    assert application.integrations.playback_transport is None
    assert application.integrations.gateway_lifecycle is None
    assert tuple(
        provider.key for provider in application.integrations.catalog_providers
    ) == (
        "youtube",
        "youtube_music",
    )
    catalog_job = application.operations.jobs.descriptor(JobId.CATALOG_MAINTENANCE)
    assert catalog_job.module == "catalog"
    assert catalog_job.controls.batch_size.default == 10
    cleanup_job = application.operations.jobs.descriptor(JobId.CATALOG_CLEANUP)
    assert cleanup_job.module == "catalog"
    assert cleanup_job.controls.preview is not None
    assert cleanup_job.controls.age_days is not None
    revalidation_job = application.operations.jobs.descriptor(JobId.SOURCE_REVALIDATION)
    assert revalidation_job.module == "catalog"
    assert revalidation_job.controls.batch_size.default == 10
    paths = create_app(application).openapi()["paths"]
    assert {
        "/api/catalog/search",
        "/api/catalog/playlist",
        "/api/catalog/link",
        "/api/library/tracks",
        "/api/library/reactions",
        "/api/library/tracks/{track_id}/reaction",
        "/api/library/tracks/{track_id}/reactions",
        "/api/library/playlists",
        "/api/library/playlists/imports",
        "/api/library/playlists/{playlist_id}",
        "/api/library/playlists/{playlist_id}/duplicate",
        "/api/library/playlists/{playlist_id}/source",
        "/api/library/playlists/{playlist_id}/entries",
        "/api/library/playlists/{playlist_id}/entries/{entry_id}",
        "/api/library/playlists/{playlist_id}/entries/{entry_id}/position",
        "/api/library/playlists/{playlist_id}/queue",
    } <= paths.keys()
    assert "/api/library/playlists/{playlist_id}/order" not in paths
    assert configured == [(LogLevel.WARNING, (Path.cwd() / "data/logs").resolve(), 14)]
    asyncio.run(application.close())


def test_application_start_migrates_empty_database_and_reconciles_operators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_path = tmp_path / "access.toml"
    access_path.write_text('owner_id = "9"\nadmin_ids = ["8"]\n', encoding="utf-8")
    application = bootstrap(
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "ACCESS_PATH": str(access_path),
        }
    )

    async def scenario() -> None:
        await application.start()
        api = create_app(application)
        transport = httpx.ASGITransport(app=api)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:3000",
        ) as client:
            assert (await client.get("/health")).json() == {"status": "alive"}
            assert (await client.get("/ready")).json() == {
                "status": "ready",
                "checks": {
                    "database": "ready",
                    "player": "ready",
                    "listening": "ready",
                    "discord": "disabled",
                    "playback": "disabled",
                },
            }

            async def unavailable(_database: Database) -> None:
                raise SQLAlchemyError("database unavailable")

            monkeypatch.setattr(Database, "ping", unavailable)
            unavailable_response = await client.get("/ready")
            assert unavailable_response.status_code == 503
            assert unavailable_response.json() == {
                "status": "unavailable",
                "checks": {
                    "database": "unavailable",
                    "player": "ready",
                    "listening": "ready",
                    "discord": "disabled",
                    "playback": "disabled",
                },
            }
            assert (await client.get("/health")).json() == {"status": "alive"}

        async with application.database.engine.connect() as connection:
            tables = await connection.run_sync(_table_names)
        assert {
            "users",
            "login_attempts",
            "browser_sessions",
            "access_events",
        } <= tables
        async with UnitOfWork(application.database.sessions) as work:
            configured_owner = await UserRepository(work.session).get_by_discord_id("9")
        assert configured_owner is not None
        owner = await application.users.access.require_admin(configured_owner.id)
        assert owner.role is AccessRole.OWNER
        await application.close()

    asyncio.run(scenario())

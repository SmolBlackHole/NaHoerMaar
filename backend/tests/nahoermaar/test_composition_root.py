# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import Connection, inspect

from nahoermaar.api.app import create_app
from nahoermaar.bootstrap import bootstrap
from nahoermaar.catalog.service import CatalogService
from nahoermaar.config import LogLevel
from nahoermaar.messaging import MessageBus
from nahoermaar.users.domain import AccessRole


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def test_bootstrap_loads_settings_and_composes_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured: list[LogLevel] = []
    monkeypatch.setattr("nahoermaar.bootstrap.configure_logging", configured.append)
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
    assert application.access.operators.owner_id == "9"
    assert isinstance(application.bus, MessageBus)
    assert isinstance(application.catalog, CatalogService)
    paths = create_app(application).openapi()["paths"]
    assert {
        "/api/catalog/search",
        "/api/catalog/playlist",
        "/api/catalog/link",
    } <= paths.keys()
    assert configured == [LogLevel.WARNING]
    asyncio.run(application.close())


def test_application_start_migrates_empty_database_and_reconciles_operators(
    tmp_path: Path,
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
        async with application.database.engine.connect() as connection:
            tables = await connection.run_sync(_table_names)
        assert {
            "users",
            "login_attempts",
            "browser_sessions",
            "access_events",
        } <= tables
        owner = await application.access.require_admin(
            (await application.auth.profile_by_discord_id("9")).id
        )
        assert owner.role is AccessRole.OWNER
        await application.close()

    asyncio.run(scenario())

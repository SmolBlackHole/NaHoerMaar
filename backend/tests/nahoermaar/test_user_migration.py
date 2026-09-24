# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from nahoermaar.database.core import Database

ROOT = Path(__file__).parents[3]
USER_TABLES = {
    "users",
    "discord_identities",
    "user_profiles",
    "user_preferences",
    "login_attempts",
    "browser_sessions",
    "access_events",
}


def test_initial_user_migration_upgrades_and_downgrades_fresh_postgresql() -> None:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    command.upgrade(configuration, "head")
    command.check(configuration)
    database = Database(database_url)

    async def inspect_upgrade() -> None:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda value: set(inspect(value).get_table_names())
            )
            revision = await connection.run_sync(
                lambda value: MigrationContext.configure(value).get_current_revision()
            )
            assert USER_TABLES <= tables
            assert revision == "0001_users"

    try:
        asyncio.run(inspect_upgrade())
    finally:
        asyncio.run(database.close())

    command.downgrade(configuration, "base")
    database = Database(database_url)

    async def inspect_downgrade() -> None:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda value: set(inspect(value).get_table_names())
            )
            assert USER_TABLES.isdisjoint(tables)

    try:
        asyncio.run(inspect_downgrade())
    finally:
        asyncio.run(database.close())

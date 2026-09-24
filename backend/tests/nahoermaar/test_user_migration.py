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
APPLICATION_TABLES = {
    "users",
    "discord_identities",
    "user_profiles",
    "user_preferences",
    "login_attempts",
    "browser_sessions",
    "access_events",
    "artists",
    "artist_sources",
    "tracks",
    "track_sources",
    "track_artists",
    "track_source_artists",
    "discovery_keys",
    "discovery_snapshots",
    "discovery_results",
    "listening_sessions",
    "operation_receipts",
    "operation_receipt_entries",
    "queue_undos",
    "queue_undo_groups",
    "queue_undo_entries",
    "radio_runs",
    "radio_candidates",
    "radio_exclusions",
    "track_requests",
    "player_checkpoints",
    "queue_entries",
}


def test_initial_migration_upgrades_and_downgrades_fresh_postgresql() -> None:
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
            assert APPLICATION_TABLES <= tables
            assert revision == "0001_initial"

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
            assert APPLICATION_TABLES.isdisjoint(tables)

    try:
        asyncio.run(inspect_downgrade())
    finally:
        asyncio.run(database.close())

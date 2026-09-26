# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection

from engine.database import database_url
from nahoermaar.database.core import Database
from nahoermaar.database import schema


def test_alembic_uses_new_schema_with_single_initial_revision() -> None:
    root = Path(__file__).parents[3]
    configuration = Config(root / "alembic.ini")
    scripts = ScriptDirectory.from_config(configuration)

    assert (
        Path(scripts.dir).resolve()
        == (root / "backend/src/nahoermaar/database/migrations").resolve()
    )
    assert scripts.get_heads() == ["0001_initial"]


def test_runtime_migration_uses_packaged_scripts() -> None:
    database = Database(os.environ["DATABASE_URL"])

    async def migrate_and_read_revision() -> str | None:
        await schema.migrate(database.engine)
        async with database.engine.connect() as connection:
            return await connection.run_sync(_current_revision)

    try:
        revision = asyncio.run(migrate_and_read_revision())
    finally:
        asyncio.run(database.close())

    assert revision == "0001_initial"


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def test_initial_migration_round_trip(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    configuration = Config(root / "alembic.ini")
    url = database_url(tmp_path / "migration-round-trip")
    configuration.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    command.upgrade(configuration, "head")
    command.check(configuration)
    command.downgrade(configuration, "base")
    command.upgrade(configuration, "head")
    command.check(configuration)

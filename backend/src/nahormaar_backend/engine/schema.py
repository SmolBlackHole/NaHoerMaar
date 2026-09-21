# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Versioned schema initialization and the single listening-session identity."""

from pathlib import Path
from typing import cast
from uuid import UUID

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, MetaData, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..persistence import models as models  # register unchanged account tables
from ..persistence.database import Base as AccountBase
from .domain.sessions import ListeningSession
from .persistence import (
    Base,
    ListeningSessionRepository,
    database_engine,
    write_transaction,
)

REVISION = "engine_0001"


def metadata() -> MetaData:
    """Account storage is unchanged; playback uses only the new engine tables."""
    result = MetaData()
    for table in (
        *Base.metadata.sorted_tables,
        AccountBase.metadata.tables["accounts"],
        AccountBase.metadata.tables["sessions"],
        AccountBase.metadata.tables["login_attempts"],
    ):
        table.to_metadata(result)
    return result


def upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).with_name("migrations"))
    )
    config.attributes["connection"] = connection
    command.upgrade(config, REVISION)


async def initialize(path: Path) -> UUID:
    """Initialize an empty database or reopen the one shared listening session.

    An old/foreign schema is never upgraded or replaced. Schema creation and
    session identity commit together, so a failed first start can be retried.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = database_engine(path)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        async with write_transaction(sessions) as database:
            connection = await database.connection()
            heads = await connection.run_sync(
                lambda conn: MigrationContext.configure(conn).get_current_heads()
            )
            if not heads:
                tables = await connection.run_sync(
                    lambda conn: (
                        inspect(conn).get_table_names() + inspect(conn).get_view_names()
                    )
                )
                if tables:
                    raise ValueError(
                        "Database is not empty; choose a fresh DATABASE_PATH."
                    )
                await connection.run_sync(upgrade)
            elif heads != (REVISION,):
                raise ValueError(
                    "Expected engine schema; choose a fresh DATABASE_PATH."
                )
            identifiers = tuple(
                await database.scalars(
                    select(Base.metadata.tables["listening_sessions"].c.id)
                )
            )
            if len(identifiers) > 1:
                raise ValueError("This runtime supports exactly one listening session.")
            if identifiers:
                return cast(UUID, identifiers[0])
            settings = ListeningSession()
            await ListeningSessionRepository(database).add(settings)
            return settings.id
    finally:
        await engine.dispose()

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Shared relational schema primitives and startup migration."""

import logging
from pathlib import Path
from time import perf_counter

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, MetaData
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)
_LOGGER = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base for private ORM mappings owned by feature repositories."""

    metadata = metadata


async def migrate(
    engine: AsyncEngine,
    configuration_path: Path | None = None,
) -> None:
    """Upgrade the configured database through the application's async engine."""
    path = configuration_path or Path(__file__).resolve().parents[4] / "alembic.ini"
    started_at = perf_counter()
    _LOGGER.info("database.migration_started configuration=%s", path)
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, path)
    _LOGGER.info(
        "database.migration_completed duration_ms=%.1f",
        (perf_counter() - started_at) * 1000,
    )


def _upgrade(connection: Connection, configuration_path: Path) -> None:
    configuration = Config(str(configuration_path))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, "head")

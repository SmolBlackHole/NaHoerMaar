# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Alembic environment for the new application schema."""

import asyncio
from collections.abc import Mapping

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from nahoermaar.config import Settings
from nahoermaar.database.schema import Base


def configure(connection: Connection) -> None:
    """Run migrations on an existing synchronous Alembic connection."""
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def database_url() -> str:
    """Resolve the CLI URL without configuring the application runtime."""
    configured = context.config.get_main_option("sqlalchemy.url")
    return configured or Settings.load().database_url


def offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=Base.metadata,
        compare_type=True,
        literal_binds=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online(configuration: Mapping[str, str]) -> None:
    values = dict(configuration)
    values["sqlalchemy.url"] = database_url()
    engine = async_engine_from_config(
        values,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure)
    finally:
        await engine.dispose()


provided_connection = context.config.attributes.get("connection")
if context.is_offline_mode():
    offline()
elif isinstance(provided_connection, Connection):
    configure(provided_connection)
else:
    section = context.config.get_section(context.config.config_ini_section) or {}
    asyncio.run(online(section))

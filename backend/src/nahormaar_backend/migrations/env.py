# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Alembic environment for application startup and the standard CLI."""

from pathlib import Path
from typing import cast

from alembic import context
from sqlalchemy import Connection, make_url

from nahormaar_backend import storage as storage  # register all mapped tables
from nahormaar_backend.database import Base, database_engine

config = context.config


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


shared = cast(Connection | None, config.attributes.get("connection"))
if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif shared is not None:
    run(shared)
else:
    url = make_url(config.get_main_option("sqlalchemy.url") or "")
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("Configure a SQLite sqlalchemy.url in alembic.ini.")
    engine = database_engine(Path(url.database))
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()

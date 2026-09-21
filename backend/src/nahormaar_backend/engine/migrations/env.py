# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Alembic environment for engine startup and the standard CLI."""

from pathlib import Path

from alembic import context
from sqlalchemy import Connection, make_url

from nahormaar_backend.engine.schema import metadata
from nahormaar_backend.persistence.database import database_engine


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata(),
        render_as_batch=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


config = context.config
connection = config.attributes.get("connection")
if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata(),
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif isinstance(connection, Connection):
    run(connection)
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

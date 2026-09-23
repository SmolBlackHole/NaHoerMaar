# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Alembic environment for engine startup and the standard CLI."""

from alembic import context
from sqlalchemy import Connection, make_url

from nahormaar_backend.config import database_url, environment_values
from nahormaar_backend.engine.schema import metadata
from nahormaar_backend.persistence.database import database_engine


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata(),
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


config = context.config
connection = config.attributes.get("connection")
configured_url = config.get_main_option("sqlalchemy.url") or database_url(
    environment_values()
)
if context.is_offline_mode():
    url = make_url(configured_url)
    context.configure(
        url=url,
        target_metadata=metadata(),
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif isinstance(connection, Connection):
    run(connection)
else:
    url = make_url(configured_url)
    engine = database_engine(url)
    try:
        with engine.begin() as migration_connection:
            run(migration_connection)
    finally:
        engine.dispose()

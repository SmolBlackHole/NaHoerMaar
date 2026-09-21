# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Only a caller-supplied connection may migrate the isolated engine database."""

from alembic import context
from sqlalchemy import Connection

from nahormaar_backend.engine.schema import metadata

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise ValueError("Supply an explicit engine database connection.")
context.configure(
    connection=connection, target_metadata=metadata(), transactional_ddl=True
)
with context.begin_transaction():
    context.run_migrations()

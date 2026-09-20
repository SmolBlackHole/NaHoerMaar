# SPDX-FileCopyrightText: 2026 SmolBlackHole
# SPDX-License-Identifier: MPL-2.0

"""Run Alembic on the store's SQLAlchemy transaction."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, inspect

from ..database import Base


def upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent))
    config.attributes["connection"] = connection
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if inspector.get_view_names():
        raise ValueError("Unexpected database views.")
    if "alembic_version" not in tables and tables:
        # Adopt databases created before Alembic; no future schema uses this marker.
        legacy = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        if legacy not in (1, 2, 3, 4, 5):
            raise ValueError(f"Unsupported legacy database schema ({legacy}).")
        command.stamp(config, f"000{legacy}")
    command.upgrade(config, "head")
    inspector = inspect(connection)
    if set(inspector.get_table_names()) != set(Base.metadata.tables) | {
        "alembic_version"
    }:
        raise ValueError("Unexpected database tables.")
    for table in Base.metadata.sorted_tables:
        if [item["name"] for item in inspector.get_columns(table.name)] != list(
            table.columns.keys()
        ):
            raise ValueError(f"Unexpected columns in {table.name}.")

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Shared SQLAlchemy metadata and SQLite connections."""

import sqlite3
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import ConnectionPoolEntry


class Base(DeclarativeBase):
    pass


def _foreign_keys(connection: sqlite3.Connection, record: ConnectionPoolEntry) -> None:
    connection.autocommit = True
    try:
        connection.execute("PRAGMA foreign_keys = ON").close()
    finally:
        connection.autocommit = False


def database_engine(path: Path, timeout: float = 5.0) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"autocommit": False, "timeout": timeout},
    )
    event.listen(engine, "connect", _foreign_keys)
    return engine

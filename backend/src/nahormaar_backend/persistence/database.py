# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Shared SQLAlchemy metadata and PostgreSQL connections."""

from sqlalchemy import URL, Engine, create_engine, make_url
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def database_engine(database: str | URL) -> Engine:
    """Create the synchronous PostgreSQL engine used by account storage."""
    url = make_url(database)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Database URL must use PostgreSQL with psycopg.")
    return create_engine(url, pool_pre_ping=True)

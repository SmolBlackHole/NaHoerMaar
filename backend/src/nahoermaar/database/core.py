# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Ownership of the application's asynchronous database resources."""

import logging
from time import perf_counter

from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


_LOGGER = logging.getLogger(__name__)


class DatabaseConfigurationError(ValueError):
    """Raised when a database URL cannot back the application."""


class Database:
    """Own one engine and one session factory for the application process."""

    __slots__ = ("_engine", "_sessions")

    def __init__(self, database_url: str | URL) -> None:
        try:
            url = make_url(database_url)
        except ArgumentError as error:
            raise DatabaseConfigurationError("DATABASE_URL is invalid.") from error
        if url.drivername != "postgresql+psycopg":
            raise DatabaseConfigurationError(
                "DATABASE_URL must use PostgreSQL with psycopg."
            )
        self._engine = create_async_engine(url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            autobegin=False,
        )
        _LOGGER.info(
            "database.configured driver=%s host=%s port=%s database=%s",
            url.drivername,
            url.host,
            url.port,
            url.database,
        )

    @property
    def engine(self) -> AsyncEngine:
        """Return the owned engine for schema and lifecycle operations."""
        return self._engine

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        """Return the shared factory for explicit database transactions."""
        return self._sessions

    async def ping(self) -> None:
        """Verify that PostgreSQL accepts a connection and a trivial query."""
        async with self._engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")

    async def close(self) -> None:
        """Release connections owned by this database instance."""
        started_at = perf_counter()
        await self._engine.dispose()
        _LOGGER.info(
            "database.closed duration_ms=%.1f",
            (perf_counter() - started_at) * 1000,
        )

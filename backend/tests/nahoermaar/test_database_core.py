# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nahoermaar.database.core import Database, DatabaseConfigurationError


def test_database_owns_async_engine_and_session_factory() -> None:
    database = Database("postgresql+psycopg://app:secret@database/nahoermaar")

    session = database.sessions()
    assert isinstance(session, AsyncSession)
    assert database.engine.url.drivername == "postgresql+psycopg"
    assert session.sync_session.expire_on_commit is False
    assert session.sync_session.autobegin is False

    asyncio.run(session.close())
    asyncio.run(database.close())


@pytest.mark.parametrize(
    "database_url",
    [
        "not a database URL",
        "sqlite+aiosqlite:///nahoermaar.sqlite3",
        "postgresql://app:secret@database/nahoermaar",
    ],
)
def test_database_rejects_unsupported_urls(database_url: str) -> None:
    with pytest.raises(DatabaseConfigurationError):
        Database(database_url)

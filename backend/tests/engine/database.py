# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nahormaar_backend.engine.persistence import Base, database_engine


@asynccontextmanager
async def isolated_database(
    path: Path,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = database_engine(path)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
    finally:
        await engine.dispose()

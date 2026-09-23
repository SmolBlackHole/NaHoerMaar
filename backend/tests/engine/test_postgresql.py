# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import async_sessionmaker

from nahormaar_backend.engine.domain.sessions import ListeningSession
from nahormaar_backend.engine.persistence import (
    ListeningSessionRepository,
    database_engine,
    write_transaction,
)
from nahormaar_backend.engine.schema import REVISION, initialize
from nahormaar_backend.persistence.accounts import Accounts
from .database import database_url


def test_postgresql_schema_transactions_and_account_store(tmp_path: Path) -> None:
    database = database_url(tmp_path / "postgresql")

    async def scenario() -> None:
        session_id = await initialize(database)
        engine = database_engine(database)
        try:
            sessions = async_sessionmaker(
                engine, expire_on_commit=False, autobegin=False
            )
            async with write_transaction(sessions) as session:
                repository = ListeningSessionRepository(session)
                current = await repository.get(session_id)
                assert current == ListeningSession(id=session_id)
                await repository.update(
                    ListeningSession(id=session_id, volume=0.42, revision=1)
                )
            async with engine.connect() as connection:
                assert (
                    await connection.run_sync(
                        lambda conn: MigrationContext.configure(
                            conn
                        ).get_current_revision()
                    )
                    == REVISION
                )
        finally:
            await engine.dispose()

        accounts = Accounts(database)
        try:
            now = datetime.now(UTC).timestamp()
            accounts.create_session(
                "243718053362270208",
                "PostgreSQL test",
                "0001",
                "postgresql-test-token",
                now + 60,
                now,
                None,
            )
            account = accounts.session("postgresql-test-token", now)
            assert account and account[0].profile.name == "PostgreSQL test"
        finally:
            accounts.close()

    asyncio.run(scenario())

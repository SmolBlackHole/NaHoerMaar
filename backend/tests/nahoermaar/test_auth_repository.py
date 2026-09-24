# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

from nahoermaar.database.core import Database
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import (
    AccessRole,
    BrowserSession,
    DiscordIdentity,
    LoginAttempt,
    User,
    UserId,
)
from nahoermaar.users.repository import AuthRepository, UserRepository

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ROOT = Path(__file__).parents[3]


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_login_attempts_are_browser_bound_expiring_and_single_use() -> None:
    database = _database()
    attempt = LoginAttempt(
        b"s" * 32, b"b" * 32, "v" * 64, NOW, NOW + timedelta(minutes=10)
    )

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            assert await AuthRepository(work.session).begin_login(attempt)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            auth = AuthRepository(work.session)
            assert await auth.consume_login(b"s" * 32, b"x" * 32, NOW) is None
            assert await auth.consume_login(b"s" * 32, b"b" * 32, NOW) == "v" * 64
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            assert (
                await AuthRepository(work.session).consume_login(
                    b"s" * 32, b"b" * 32, NOW
                )
                is None
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_browser_sessions_reference_internal_users_and_can_be_rotated() -> None:
    database = _database()
    user = User(
        UserId(uuid4()),
        DiscordIdentity("9"),
        NOW,
        NOW,
        role=AccessRole.OWNER,
    )
    first = BrowserSession(b"a" * 32, user.id, NOW, NOW + timedelta(days=7))
    second = BrowserSession(b"b" * 32, user.id, NOW, NOW + timedelta(days=7))

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            UserRepository(work.session).add(user)
            await AuthRepository(work.session).create_session(first)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            auth = AuthRepository(work.session)
            assert await auth.session(first.token_hash, NOW) == first
            await auth.create_session(second, previous_hash=first.token_hash)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            auth = AuthRepository(work.session)
            assert await auth.session(first.token_hash, NOW) is None
            assert await auth.session(second.token_hash, NOW) == second
            await auth.delete_user_sessions(user.id)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            assert (
                await AuthRepository(work.session).session(second.token_hash, NOW)
                is None
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

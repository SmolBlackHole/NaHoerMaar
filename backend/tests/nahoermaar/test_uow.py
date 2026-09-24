# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nahoermaar.database.uow import UnitOfWork


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def begin(self) -> None:
        self.calls.append("begin")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")

    async def close(self) -> None:
        self.calls.append("close")


def unit_of_work(session: FakeSession) -> UnitOfWork:
    return UnitOfWork(lambda: cast(AsyncSession, session))


def test_unit_of_work_commits_and_closes() -> None:
    session = FakeSession()

    async def execute() -> None:
        async with unit_of_work(session) as work:
            assert work.session is cast(AsyncSession, session)
            await work.commit()

    asyncio.run(execute())

    assert session.calls == ["begin", "commit", "close"]


def test_unit_of_work_rolls_back_uncommitted_work() -> None:
    session = FakeSession()

    async def execute() -> None:
        async with unit_of_work(session):
            pass

    asyncio.run(execute())

    assert session.calls == ["begin", "rollback", "close"]


def test_unit_of_work_rolls_back_after_failure() -> None:
    session = FakeSession()

    async def execute() -> None:
        with pytest.raises(RuntimeError, match="failed"):
            async with unit_of_work(session):
                raise RuntimeError("failed")

    asyncio.run(execute())

    assert session.calls == ["begin", "rollback", "close"]


def test_unit_of_work_hides_session_outside_context() -> None:
    work = unit_of_work(FakeSession())

    with pytest.raises(RuntimeError, match="not active"):
        _ = work.session

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest

from nahormaar_backend.application.access import Access, Operators
from nahormaar_backend.domain.access import AccessRole
from nahormaar_backend.domain.identity import AuthError
from nahormaar_backend.persistence.database import database_engine
from nahormaar_backend.engine.schema import upgrade
from engine.database import database_url


def access_service(tmp_path: Path) -> Access:
    database = database_url(tmp_path / "access.db")
    engine = database_engine(database)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    ticks = count()
    return Access(
        database,
        Operators("1", ("2",)),
        clock=lambda: (
            datetime(2026, 9, 23, tzinfo=UTC) + timedelta(microseconds=next(ticks))
        ),
    )


def test_roles_idempotency_and_immutable_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        access = access_service(tmp_path)
        try:
            assert await access.require("1") is AccessRole.OWNER
            assert await access.require("2") is AccessRole.ADMIN
            assert await access.grant("1", "3")
            assert not await access.grant("1", "3")
            assert await access.require("3") is AccessRole.USER
            assert access.accounts.access_role("3") is AccessRole.USER
            assert [event.action for event in await access.history()] == ["granted"]
            assert await access.revoke("1", "3")
            assert not await access.revoke("1", "3")
            assert access.accounts.access_role("3") is None
            assert [event.action for event in await access.history()] == [
                "revoked",
                "granted",
            ]
            with pytest.raises(AuthError, match="access_denied"):
                await access.require("3")
        finally:
            await access.close()

    asyncio.run(scenario())


def test_admin_can_revoke_only_grants_they_created(tmp_path: Path) -> None:
    async def scenario() -> None:
        access = access_service(tmp_path)
        try:
            assert await access.grant("2", "3")
            assert await access.grant("1", "4")
            assert await access.revoke("2", "3")
            with pytest.raises(AuthError, match="grant_not_owned"):
                await access.revoke("2", "4")
            assert await access.revoke("1", "4")
            for operator in ("1", "2"):
                with pytest.raises(
                    AuthError, match="operator_access_managed_in_config"
                ):
                    await access.revoke("1", operator)
        finally:
            await access.close()

    asyncio.run(scenario())


def test_concurrent_grants_create_one_row_and_one_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        access = access_service(tmp_path)
        try:
            results = await asyncio.gather(*(access.grant("1", "3") for _ in range(8)))
            assert results.count(True) == 1
            assert len(await access.grants()) == 1
            assert len(await access.history()) == 1
        finally:
            await access.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        "",
        "broken [",
        'owner_id = "0"',
        'owner_id = "1"\nadmin_ids = ["1"]',
        'owner_id = "1"\nextra = true',
    ],
)
def test_operator_configuration_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "access.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AuthError, match="access_unavailable"):
        Operators.load(path)

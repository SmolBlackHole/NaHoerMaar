# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from nahoermaar.database.core import Database
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import AccessRole, AuthError, AuthErrorCode, UserProfile
from nahoermaar.users.repository import UserRepository
from nahoermaar.users.service import (
    AccessService,
    AuthService,
    Operators,
    ProvidedDiscordIdentity,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ROOT = Path(__file__).parents[3]


class Provider:
    state = ""
    verifier = ""

    async def authorization_url(self, state: str, verifier: str) -> str:
        self.state = state
        self.verifier = verifier
        return f"https://discord.example/authorize?state={state}"

    async def identity(self, code: str, verifier: str) -> ProvidedDiscordIdentity:
        assert code == "oauth-code"
        assert verifier == self.verifier
        return ProvidedDiscordIdentity("7", "Discord name", "avatar-hash")


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_operators_grant_login_profile_and_revocation_share_internal_users() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    provider = Provider()
    access = AccessService(units, Operators("9", ("8",)), clock=lambda: NOW)
    auth = AuthService(units, provider, clock=lambda: NOW)

    async def scenario() -> None:
        startup_changes = await access.reconcile()
        assert {change.role_after for change in startup_changes} == {
            AccessRole.OWNER,
            AccessRole.ADMIN,
        }

        async with units() as work:
            owner = await UserRepository(work.session).get_by_discord_id("9")
        assert owner is not None
        assert owner.role is AccessRole.OWNER
        assert [
            (user.discord.discord_id, user.role)
            for user in await access.operator_users()
        ] == [
            ("9", AccessRole.OWNER),
            ("8", AccessRole.ADMIN),
        ]
        grant = await access.grant(owner.id, "7")
        assert grant is not None
        assert grant.role_after is AccessRole.USER

        async with units() as work:
            listener = await UserRepository(work.session).get_by_discord_id("7")
        assert listener is not None
        assert not listener.discord.complete
        assert listener.access_granted_by == owner.id

        started = await auth.begin(None)
        completed = await auth.complete(
            state=provider.state,
            browser_token=started.browser_token,
            code="oauth-code",
            error=None,
            previous_session=None,
        )
        assert completed.user.id == listener.id
        assert completed.user.discord.username == "Discord name"
        assert not completed.user.profile_complete

        saved = await auth.save_profile(
            completed.user.id, UserProfile("Local name", "12ab")
        )
        assert saved.profile.display_name == "Local name"
        assert saved.discord.username == "Discord name"
        assert (await auth.authenticate(completed.session_token)).user == saved

        revoked = await access.revoke(owner.id, "7")
        assert revoked is not None
        assert revoked.role_after is None
        with pytest.raises(AuthError) as error:
            await auth.authenticate(completed.session_token)
        assert error.value.code is AuthErrorCode.SIGNED_OUT

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_reconciliation_makes_privileged_roles_match_configuration_exactly() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    async def scenario() -> None:
        await AccessService(
            units, Operators("9", ("8",)), clock=lambda: NOW
        ).reconcile()
        changes = await AccessService(
            units, Operators("9", ("6",)), clock=lambda: NOW
        ).reconcile()
        assert {(event.role_before, event.role_after) for event in changes} == {
            (AccessRole.ADMIN, None),
            (None, AccessRole.ADMIN),
        }

        async with units() as work:
            users = UserRepository(work.session)
            former = await users.get_by_discord_id("8")
            current = await users.get_by_discord_id("6")
        assert former is not None and former.role is None
        assert current is not None and current.role is AccessRole.ADMIN

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_admin_may_revoke_only_its_own_grant() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    access = AccessService(units, Operators("9", ("8",)), clock=lambda: NOW)

    async def scenario() -> None:
        await access.reconcile()
        async with units() as work:
            users = UserRepository(work.session)
            owner = await users.get_by_discord_id("9")
            admin = await users.get_by_discord_id("8")
        assert owner is not None and admin is not None
        await access.grant(owner.id, "7")
        with pytest.raises(AuthError) as error:
            await access.revoke(admin.id, "7")
        assert error.value.code is AuthErrorCode.GRANT_NOT_OWNED
        assert await access.revoke(owner.id, "7") is not None

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_invalid_operator_file_fails_closed(tmp_path: Path) -> None:
    access_path = tmp_path / "access.toml"
    access_path.write_text('owner_id = "not-a-discord-id"\n', encoding="utf-8")

    with pytest.raises(AuthError) as error:
        Operators.load(access_path)

    assert error.value.code is AuthErrorCode.ACCESS_UNAVAILABLE

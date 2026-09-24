# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import JSON, insert
from sqlalchemy.exc import IntegrityError

from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import (
    AccessRole,
    Appearance,
    AppearanceMode,
    DiscordIdentity,
    FontFamily,
    IconSet,
    NeutralColor,
    PrimaryColor,
    TextSize,
    User,
    UserId,
    UserProfile,
)
from nahoermaar.users.repository import UserNotFoundError, UserRepository

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ROOT = Path(__file__).parents[3]


def _migrate() -> str:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return database_url


def _user(
    discord_id: str,
    *,
    role: AccessRole | None = None,
    granted_by: UserId | None = None,
) -> User:
    return User(
        id=UserId(uuid4()),
        discord=DiscordIdentity(discord_id),
        created_at=NOW,
        updated_at=NOW,
        role=role,
        access_granted_by=granted_by,
        access_granted_at=NOW if granted_by is not None else None,
    )


def test_user_schema_is_relational_and_references_internal_user_ids() -> None:
    tables = Base.metadata.tables

    assert {
        "users",
        "discord_identities",
        "user_profiles",
        "user_preferences",
        "login_attempts",
        "browser_sessions",
        "access_events",
    }.issubset(tables)
    assert all(
        not isinstance(column.type, JSON)
        for table_name in (
            "users",
            "discord_identities",
            "user_profiles",
            "user_preferences",
            "login_attempts",
            "browser_sessions",
            "access_events",
        )
        for column in tables[table_name].columns
    )
    grant_foreign_key = next(iter(tables["users"].c.access_granted_by.foreign_keys))
    assert grant_foreign_key.target_fullname == "users.id"
    assert (
        next(iter(tables["discord_identities"].c.user_id.foreign_keys)).target_fullname
        == "users.id"
    )


def test_repository_round_trips_and_updates_the_complete_aggregate() -> None:
    database_url = _migrate()
    database = Database(database_url)
    operator = User(
        id=UserId(uuid4()),
        discord=DiscordIdentity(
            "1377708476259897478",
            "operator",
            "a_operator",
            NOW,
        ),
        profile=UserProfile("Operator", "021a"),
        appearance=Appearance(
            mode=AppearanceMode.SYSTEM,
            artwork_colors=False,
            primary_color=PrimaryColor.BLUE,
            neutral_color=NeutralColor.SLATE,
            font_family=FontFamily.INTER,
            icon_set=IconSet.TABLER,
            text_size=TextSize.LARGE,
        ),
        created_at=NOW,
        updated_at=NOW,
        role=AccessRole.ADMIN,
        last_login_at=NOW,
    )
    listener = _user(
        "243718053362270208",
        role=AccessRole.USER,
        granted_by=operator.id,
    )

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            users = UserRepository(work.session)
            users.add(operator)
            users.add(listener)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            users = UserRepository(work.session)
            assert await users.get(operator.id) == operator
            assert (
                await users.get_by_discord_id(listener.discord.discord_id) == listener
            )

            updated = replace(
                listener,
                discord=DiscordIdentity(
                    listener.discord.discord_id,
                    "listener",
                    None,
                    NOW + timedelta(minutes=1),
                ),
                profile=UserProfile("Local listener", "123b"),
                appearance=Appearance(
                    mode=AppearanceMode.TIME,
                    primary_color=PrimaryColor.AMBER,
                    neutral_color=NeutralColor.STONE,
                    font_family=FontFamily.PUBLIC_SANS,
                    icon_set=IconSet.PHOSPHOR,
                    text_size=TextSize.SMALL,
                ),
                updated_at=NOW + timedelta(minutes=1),
                last_login_at=NOW + timedelta(minutes=1),
            )
            await users.update(updated)
            await work.commit()

        async with UnitOfWork(database.sessions) as work:
            stored = await UserRepository(work.session).get(listener.id)
            assert stored == updated
            assert stored is not None
            assert stored.discord.username == "listener"
            assert stored.profile.display_name == "Local listener"

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_repository_leaves_commit_and_rollback_to_the_unit_of_work() -> None:
    database = Database(_migrate())
    user = _user("243718053362270209")

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            UserRepository(work.session).add(user)

        async with UnitOfWork(database.sessions) as work:
            assert await UserRepository(work.session).get(user.id) is None

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_repository_reports_missing_updates_and_database_rejects_duplicates() -> None:
    database = Database(_migrate())
    first = _user("243718053362270210")
    duplicate = _user(first.discord.discord_id)

    async def scenario() -> None:
        async with UnitOfWork(database.sessions) as work:
            with pytest.raises(UserNotFoundError):
                await UserRepository(work.session).update(first)

        with pytest.raises(IntegrityError):
            async with UnitOfWork(database.sessions) as work:
                users = UserRepository(work.session)
                users.add(first)
                users.add(duplicate)
                await work.commit()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_database_rejects_user_role_without_grant_metadata() -> None:
    database = Database(_migrate())
    users = Base.metadata.tables["users"]

    async def scenario() -> None:
        with pytest.raises(IntegrityError):
            async with UnitOfWork(database.sessions) as work:
                await work.session.execute(
                    insert(users).values(
                        id=uuid4(),
                        role=AccessRole.USER,
                        access_granted_by=None,
                        access_granted_at=None,
                        created_at=NOW,
                        updated_at=NOW,
                        last_login_at=None,
                    )
                )
                await work.commit()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

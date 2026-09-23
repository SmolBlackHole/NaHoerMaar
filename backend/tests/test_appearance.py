# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path

from nahormaar_backend.application.auth import SESSION_COOKIE, csrf_token, digest
from nahormaar_backend.domain.access import AccessRole
from nahormaar_backend.domain.preferences import Appearance
from nahormaar_backend.persistence.accounts import Accounts
from engine.test_catalog import TIME
from engine.test_engine_api import TOKEN, fixture


def test_appearance_is_saved_per_account_and_survives_restart(tmp_path: Path) -> None:
    appearance = Appearance(mode="light", primaryColor="amber", fontFamily="Inter")

    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, _provider, _audio):
            saved = await client.put(
                "/api/profile/appearance", json=appearance.model_dump()
            )
            assert saved.status_code == 200
            assert saved.json() == appearance.model_dump()
            assert (await client.get("/api/auth/session")).json()[
                "appearance"
            ] == saved.json()
            other_token = "b" * 43
            await services.access.grant("9", "2")
            services.auth.accounts.create_session(
                "2",
                "Other listener",
                "0001",
                AccessRole.USER,
                digest(other_token),
                TIME.timestamp() + 3600,
                TIME.timestamp(),
                None,
            )
            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE, other_token)
            client.headers["X-CSRF-Token"] = csrf_token(other_token)
            assert (await client.get("/api/auth/session")).json()[
                "appearance"
            ] == Appearance().model_dump()
            assert (
                await client.put(
                    "/api/profile/appearance", json={"primaryColor": "rose"}
                )
            ).status_code == 200
        accounts = Accounts(services.auth.settings.database_url)
        try:
            saved_account = accounts.session(digest(TOKEN), TIME.timestamp())
            assert saved_account and saved_account[0].appearance == appearance
            other = accounts.session(digest(other_token), TIME.timestamp())
            assert other and other[0].appearance.primaryColor == "rose"
        finally:
            accounts.close()

    asyncio.run(scenario())


def test_appearance_rejects_invalid_choices_and_requires_session_csrf(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, _services, _provider, _audio):
            for invalid in (
                {"mode": "invalid"},
                {"fontFamily": "arbitrary CSS"},
                {"artworkColors": "true"},
                {"account_id": "another-account"},
            ):
                assert (
                    await client.put("/api/profile/appearance", json=invalid)
                ).status_code == 422
            assert (
                await client.put(
                    "/api/profile/appearance", json={}, headers={"X-CSRF-Token": ""}
                )
            ).status_code == 403
            assert (await client.get("/api/auth/session")).json()[
                "appearance"
            ] == Appearance().model_dump()
            client.cookies.clear()
            assert (
                await client.put("/api/profile/appearance", json={})
            ).status_code == 401

    asyncio.run(scenario())

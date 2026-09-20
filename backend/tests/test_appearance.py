# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path

from nahormaar_backend.auth import SESSION_COOKIE, csrf_token
from nahormaar_backend.preferences import Appearance
from test_api import Harness


def test_appearance_is_saved_per_account_and_survives_restart(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "player.sqlite3")
    appearance = Appearance(mode="light", primaryColor="amber", fontFamily="Inter")

    async def scenario() -> None:
        async with harness.client() as client:
            saved = await client.put(
                "/api/profile/appearance", json=appearance.model_dump()
            )
            assert saved.status_code == 200
            assert saved.json() == appearance.model_dump()
            assert (await client.get("/api/auth/session")).json()[
                "appearance"
            ] == saved.json()
            other_token = "b" * 43
            harness.account("2", other_token, "Other listener")
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
        async with harness.client() as client:
            assert (await client.get("/api/auth/session")).json()[
                "appearance"
            ] == appearance.model_dump()

    asyncio.run(scenario())


def test_appearance_rejects_invalid_choices_and_requires_session_csrf(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with Harness(tmp_path / "player.sqlite3").client() as client:
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

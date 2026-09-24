# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
from sqlalchemy import insert

from nahoermaar.api.app import create_app
from nahoermaar.catalog.service import CatalogService
from nahoermaar.bootstrap import (  # pyright: ignore[reportPrivateUsage]
    Application,
    _register_handlers,  # pyright: ignore[reportPrivateUsage]
)
from nahoermaar.config import AuthSettings, Settings
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import MessageBus
from nahoermaar.player.session import CatalogRadioResolver, PlayerSessionManager
from nahoermaar.users.service import (
    AccessService,
    AuthService,
    Operators,
    ProvidedDiscordIdentity,
    SESSION_COOKIE,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ROOT = Path(__file__).parents[3]
ORIGIN = "http://localhost:3000"


class Provider:
    state = ""
    verifier = ""

    async def authorization_url(self, state: str, verifier: str) -> str:
        self.state = state
        self.verifier = verifier
        return "https://discord.example/authorize"

    async def identity(self, code: str, verifier: str) -> ProvidedDiscordIdentity:
        assert code == "oauth-code"
        assert verifier == self.verifier
        return ProvidedDiscordIdentity("9", "Owner", None)


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_auth_profile_access_origin_and_csrf_share_one_api_boundary() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    provider = Provider()
    access = AccessService(units, Operators("9", ()), clock=lambda: NOW)
    auth = AuthService(units, provider, clock=lambda: NOW)
    bus = MessageBus()
    catalog = CatalogService(units, ())
    player = PlayerSessionManager(units, bus, CatalogRadioResolver(catalog))
    _register_handlers(bus, auth, access, player)
    settings = Settings(
        os.environ["DATABASE_URL"],
        AuthSettings(ORIGIN, "123", "secret", Path("access.toml")),
    )
    application = Application(
        settings,
        database,
        bus,
        auth,
        access,
        catalog,
        player,
    )
    app = create_app(application)

    async def scenario() -> None:
        await access.reconcile()
        await player.start()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=ORIGIN,
            follow_redirects=False,
        ) as client:
            signed_out_catalog = await client.get(
                "/api/catalog/search", params={"q": "test"}
            )
            assert signed_out_catalog.status_code == 401
            assert (await client.get("/api/player")).status_code == 401

            begin = await client.get("/api/auth/discord")
            assert begin.status_code == 302
            assert begin.headers["location"] == "https://discord.example/authorize"

            callback = await client.get(
                "/api/auth/discord/callback",
                params={"state": provider.state, "code": "oauth-code"},
            )
            assert callback.status_code == 302
            assert callback.headers["location"] == ORIGIN + "/profile"
            session_token = client.cookies.get(SESSION_COOKIE)
            current = await auth.authenticate(session_token)

            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["role"] == "owner"

            player_state = await client.get("/api/player")
            assert player_state.status_code == 200
            assert player_state.json()["queue"] == []
            assert player_state.json()["crossfade_seconds"] == 7

            rejected = await client.put(
                "/api/users/me/profile",
                json={"display_name": "Owner", "pixabot": "12ab"},
            )
            assert rejected.status_code == 403
            assert rejected.json() == {"error": "csrf_failed"}

            headers = {
                "origin": ORIGIN,
                "x-csrf-token": current.csrf,
            }
            track_id = uuid4()
            async with units() as work:
                await work.session.execute(
                    insert(Base.metadata.tables["tracks"]).values(
                        id=track_id,
                        title="API track",
                        duration_seconds=180.0,
                        artwork_url=None,
                        album_title=None,
                        release_date=None,
                        isrc=None,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await work.commit()
            operation_id = str(uuid4())
            queued = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": operation_id,
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert queued.status_code == 200
            assert queued.json()["player"]["queue"][0]["request"][
                "requested_by"
            ] == str(current.user.id)
            replayed = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": operation_id,
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["replayed"] is True
            assert len(replayed.json()["player"]["queue"]) == 1

            profile = await client.put(
                "/api/users/me/profile",
                headers=headers,
                json={"display_name": "Local owner", "pixabot": "12ab"},
            )
            assert profile.status_code == 200
            assert profile.json()["profile"]["display_name"] == "Local owner"
            assert profile.json()["discord"]["username"] == "Owner"

            operator = await client.put("/api/access/9", headers=headers)
            assert operator.status_code == 409
            assert operator.json() == {"error": "operator_access_managed_in_config"}

            grant = await client.put("/api/access/7", headers=headers)
            assert grant.status_code == 200
            assert grant.json()["role_after"] == "user"
            access_state = await client.get("/api/access")
            assert access_state.status_code == 200
            assert access_state.json()["grants"][0]["discord"]["id"] == "7"

            wrong_origin = await client.get(
                "/api/users/me", headers={"origin": "https://evil.example"}
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json() == {"error": "origin_forbidden"}
        await player.close()
        await catalog.close()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

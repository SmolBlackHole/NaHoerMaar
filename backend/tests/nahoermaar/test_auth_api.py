# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
import httpx
from sqlalchemy import insert

from nahoermaar.api.app import create_app
from nahoermaar.api.events import AuthEventView, ChangeView, event_stream
from nahoermaar.api.player import PlayerView
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
from nahoermaar.listening.service import ListeningService
from nahoermaar.observability import ContextFilter
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.player.session import CatalogRadioResolver, PlayerSessionManager
from nahoermaar.statistics.service import StatisticsService
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
    listening = ListeningService(units, bus)
    statistics = StatisticsService(units, ZoneInfo("UTC"), clock=lambda: NOW)
    logs = RecentLogBuffer()
    logs.addFilter(ContextFilter())
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logs)
    _register_handlers(bus, auth, access, player, listening)
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
        listening,
        statistics,
        logs,
    )
    app = create_app(application)
    assert "/api/events" in app.openapi()["paths"]
    assert "/api/logs" in app.openapi()["paths"]
    assert "/api/statistics/overview" in app.openapi()["paths"]
    assert "/api/statistics/users/{user_id}" in app.openapi()["paths"]

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
            assert session_token is not None
            current = await auth.authenticate(session_token)

            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["role"] == "owner"

            overview = await client.get("/api/statistics/overview")
            assert overview.status_code == 200
            assert overview.json()["totals"]["plays"] == 0
            own_statistics = await client.get(
                f"/api/statistics/users/{current.user.id}"
            )
            assert own_statistics.status_code == 200
            assert own_statistics.json()["user_id"] == str(current.user.id)
            own_profile = await client.get(f"/api/users/{current.user.id}")
            assert own_profile.status_code == 200
            assert own_profile.json()["id"] == str(current.user.id)

            player_state = await client.get("/api/player")
            assert player_state.status_code == 200
            assert player_state.json()["queue"] == []
            assert player_state.json()["crossfade_seconds"] == 7
            events = event_stream(application, session_token)
            initial = await anext(events)
            assert initial.event == "state"
            assert initial.id == "0"
            assert isinstance(initial.data, PlayerView)
            assert initial.data.queue == ()

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
            operation_id = uuid4()
            queued = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": str(operation_id),
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert queued.status_code == 200
            assert queued.json()["player"]["queue"][0]["request"][
                "requested_by"
            ] == str(current.user.id)
            change = await anext(events)
            assert change.event == "change"
            assert change.id == "1"
            assert isinstance(change.data, ChangeView)
            assert change.data.operation_id == operation_id
            assert change.data.state.queue[0].request.requested_by == current.user.id
            replayed = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": str(operation_id),
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
            granted_user_id = access_state.json()["grants"][0]["id"]
            granted_profile = await client.get(f"/api/users/{granted_user_id}")
            assert granted_profile.status_code == 200

            revoked = await client.delete("/api/access/7", headers=headers)
            assert revoked.status_code == 200
            hidden_profile = await client.get(f"/api/users/{granted_user_id}")
            assert hidden_profile.status_code == 404
            assert hidden_profile.json() == {"error": "profile_not_found"}

            process_logs = await client.get("/api/logs", params={"limit": 200})
            assert process_logs.status_code == 200
            entries = process_logs.json()["entries"]
            assert entries
            assert any(entry["actor_id"] == str(current.user.id) for entry in entries)
            assert all("q=test" not in entry["message"] for entry in entries)
            assert all("state=" not in entry["message"] for entry in entries)

            wrong_origin = await client.get(
                "/api/users/me", headers={"origin": "https://evil.example"}
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json() == {"error": "origin_forbidden"}

            waiting = asyncio.create_task(anext(events))
            await asyncio.sleep(0)
            await auth.logout(session_token)
            player.events.reauthenticate()
            auth_event = await asyncio.wait_for(waiting, timeout=1)
            assert auth_event.event == "auth"
            assert isinstance(auth_event.data, AuthEventView)
            assert auth_event.data.error == "signed_out"
            await events.aclose()
        await player.close()
        await catalog.close()

    try:
        asyncio.run(scenario())
    finally:
        root_logger.removeHandler(logs)
        root_logger.setLevel(previous_level)
        asyncio.run(database.close())

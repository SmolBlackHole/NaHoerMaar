# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nahormaar_backend.api import create_app
from nahormaar_backend.application.auth import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    SESSION_SECONDS,
    Auth,
    csrf_token,
    digest,
)
from nahormaar_backend.config import AuthSettings, ConfigurationError
from nahormaar_backend.domain.identity import AuthError, DiscordIdentity
from nahormaar_backend.integrations.discord_oauth import DiscordOAuth
from nahormaar_backend.persistence.database import database_engine
from nahormaar_backend.persistence.models import LoginRow, SessionRow
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_api import (
    AUTH_HEADERS,
    TEST_TOKEN,
    VIDEO,
    Harness,
    headers,
    mutation,
    next_state,
    running_server,
)


class Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.user = DiscordIdentity("1", "Discord name")
        self.verifier: str | None = None

    async def authorization_url(self, state: str, verifier: str) -> str:
        self.verifier = verifier
        return "https://discord.com/oauth2/authorize?" + urlencode({"state": state})

    async def identity(self, code: str, verifier: str) -> DiscordIdentity:
        assert code == "single-use-code" and verifier == self.verifier
        self.calls += 1
        return self.user


def auth_service(tmp_path: Path) -> tuple[Auth, Provider, list[float]]:
    path = tmp_path / "auth.sqlite3"
    with SQLiteStore(path):
        pass
    access = tmp_path / "access.toml"
    access.write_text('discord_ids = ["1"]', encoding="utf-8")
    provider, clock = Provider(), [time.time()]
    settings = AuthSettings(
        "https://music.example.test", "123", "test-secret", path, access
    )
    return (
        Auth(settings, ("0002", "0118"), provider=provider, clock=lambda: clock[0]),
        provider,
        clock,
    )


async def login(auth: Auth, *, previous: str | None = None) -> str:
    url, browser = await auth.begin(None)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return await auth.callback(state, browser, "single-use-code", None, previous)


@pytest.mark.parametrize(
    "outcome",
    ["success", "denied", "expired", "wrong_browser", "wrong_state", "unlisted"],
)
def test_single_use_browser_bound_login(tmp_path: Path, outcome: str) -> None:
    async def scenario() -> None:
        auth, provider, clock = auth_service(tmp_path)
        try:
            url, browser = await auth.begin(None)
            state = parse_qs(urlsplit(url).query)["state"][0]
            if outcome == "expired":
                clock[0] += 601
            if outcome == "unlisted":
                provider.user = DiscordIdentity("2", "Unlisted")
            if outcome == "success":
                replies = await asyncio.gather(
                    *(
                        auth.callback(state, browser, "single-use-code", None, None)
                        for _ in range(2)
                    ),
                    return_exceptions=True,
                )
                tokens = [reply for reply in replies if isinstance(reply, str)]
                assert len(tokens) == 1 and provider.calls == 1
                user = await auth.authenticate(tokens[0])
                assert user.account.discord_id == "1"
                assert user.expires_at == clock[0] + SESSION_SECONDS
                assert user.csrf == csrf_token(tokens[0])
                assert not user.account.profile_complete
                engine = database_engine(auth.settings.database_path)
                try:
                    with Session(engine) as db:
                        stored = db.scalar(select(SessionRow))
                        assert stored and stored.token_hash == digest(tokens[0])
                        assert db.scalar(select(LoginRow)) is None
                finally:
                    engine.dispose()
            else:
                with pytest.raises(AuthError):
                    await auth.callback(
                        "wrong" if outcome == "wrong_state" else state,
                        "wrong" if outcome == "wrong_browser" else browser,
                        "single-use-code",
                        "access_denied" if outcome == "denied" else None,
                        None,
                    )
                assert provider.calls == int(outcome == "unlisted")
        finally:
            await auth.close()

    asyncio.run(scenario())


def test_session_rotation_profile_restart_expiry_and_revocation(tmp_path: Path) -> None:
    async def scenario() -> None:
        auth, provider, clock = auth_service(tmp_path)
        token = await login(auth)
        user = await auth.authenticate(token)
        await auth.profile(user, "My own name", "0118")
        await auth.close()
        auth = Auth(
            auth.settings, auth.avatars, provider=provider, clock=lambda: clock[0]
        )
        try:
            saved = await auth.authenticate(token)
            assert saved.account.profile.id == user.account.profile.id
            assert (
                saved.account.profile.name == "My own name"
                and saved.account.profile.avatar == "0118"
            )
            assert saved.account.profile_complete
            fresh = await login(auth, previous=token)
            with pytest.raises(AuthError, match="signed_out"):
                await auth.authenticate(token)
            assert (
                await auth.authenticate(fresh)
            ).account.profile == saved.account.profile
            auth.settings.access_path.write_text("discord_ids = []", encoding="utf-8")
            with pytest.raises(AuthError, match="access_denied"):
                await auth.authenticate(fresh)
            auth.settings.access_path.write_text(
                'discord_ids = ["1"]', encoding="utf-8"
            )
            with pytest.raises(AuthError, match="signed_out"):
                await auth.authenticate(fresh)
            token = await login(auth)
            clock[0] += SESSION_SECONDS
            with pytest.raises(AuthError, match="signed_out"):
                await auth.authenticate(token)
        finally:
            await auth.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [None, "broken [", "discord_ids = [1]", 'discord_ids = ["1"]\nextra = true'],
)
def test_unreadable_access_list_fails_closed(
    tmp_path: Path, content: str | None
) -> None:
    async def scenario() -> None:
        auth, _, _ = auth_service(tmp_path)
        try:
            token = await login(auth)
            if content is None:
                auth.settings.access_path.unlink()
            else:
                auth.settings.access_path.write_text(content, encoding="utf-8")
            with pytest.raises(AuthError, match="access_unavailable"):
                await auth.authenticate(token)
        finally:
            await auth.close()

    asyncio.run(scenario())


def test_discord_oauth_uses_identify_pkce_fixed_redirect_and_discards_tokens(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    settings = AuthSettings(
        "https://music.example.test",
        "123",
        "test-secret",
        tmp_path / "db",
        tmp_path / "access",
    )
    verifier = secrets.token_urlsafe(48)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/token"):
            body = parse_qs(request.content.decode())
            assert body["code_verifier"] == [verifier]
            assert body["redirect_uri"] == [settings.redirect_uri]
            assert body["grant_type"] == ["authorization_code"]
            return httpx.Response(
                200,
                json={
                    "access_token": "temporary",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        assert request.url.path == "/api/v10/users/@me"
        assert request.headers["authorization"] == "Bearer temporary"
        return httpx.Response(
            200, json={"id": "1", "username": "Account", "global_name": "Custom"}
        )

    async def scenario() -> None:
        provider = DiscordOAuth(settings, transport=httpx.MockTransport(respond))
        query = parse_qs(
            urlsplit(await provider.authorization_url("test-state", verifier)).query
        )
        assert query["scope"] == ["identify"]
        assert query["state"] == ["test-state"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"][0] != verifier
        assert await provider.identity("code", verifier) == DiscordIdentity(
            "1", "Custom"
        )
        assert len(requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin",
    [
        "http://music.example.test",
        "https://music.example.test/path",
        "https://user:pass@music.example.test",
        "https://music.example.test?x=1",
        "null",
    ],
)
def test_public_origin_must_be_explicit_and_canonical(
    tmp_path: Path, origin: str
) -> None:
    with pytest.raises(ConfigurationError):
        AuthSettings(origin, "123", "test-secret", tmp_path / "db", tmp_path / "access")


def test_every_player_endpoint_requires_session_and_mutations_require_csrf(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        async with harness.client() as client:
            client.cookies.clear()
            for method, path in [
                ("GET", "/api/state"),
                ("GET", "/api/events"),
                ("GET", "/api/channels"),
                ("GET", "/api/catalog/search?q=music"),
                ("POST", "/api/youtube/playlists"),
                ("GET", f"/api/youtube/playlists/{uuid4()}"),
                ("POST", "/api/queue"),
                ("PUT", "/api/player/volume"),
                ("PUT", "/api/profile"),
            ]:
                response = await client.request(method, path)
                assert response.status_code == 401, path
                assert response.headers["cache-control"] == "no-store"
            client.cookies.set(SESSION_COOKIE, TEST_TOKEN)
            for extra in (
                {"X-CSRF-Token": ""},
                {"Origin": "https://evil.invalid"},
                {"Origin": ""},
            ):
                response = await client.post(
                    "/api/queue", json={"source_url": VIDEO}, headers=headers() | extra
                )
                assert response.status_code == 403
            requests: list[tuple[str, dict[str, object]]] = [
                ("/api/queue", {"source_url": VIDEO}),
                ("/api/queue/batch", {"source_urls": [VIDEO]}),
            ]
            for path, body in requests:
                assert (
                    await client.post(
                        path,
                        json=body
                        | {
                            "added_by": {
                                "id": str(uuid4()),
                                "name": "Forged",
                                "avatar": "0002",
                            }
                        },
                        headers=headers(),
                    )
                ).status_code == 422
            key = headers()
            added = mutation(
                await client.post("/api/queue", json={"source_url": VIDEO}, headers=key)
            )
            assert (
                await client.put(
                    "/api/profile", json={"name": "New name", "avatar": "0118"}
                )
            ).status_code == 200
            retry = mutation(
                await client.post("/api/queue", json={"source_url": VIDEO}, headers=key)
            )
            assert retry.replayed and retry.entry_id == added.entry_id
            assert (
                retry.snapshot.upcoming[0].added_by
                == added.snapshot.upcoming[0].added_by
            )
            state = (await client.get("/api/auth/session")).json()
            assert state["profile"]["name"] == "New name"
            assert "discord_id" not in state
            assert (await client.post("/api/auth/logout")).status_code == 204
            assert (await client.get("/api/state")).status_code == 401

    asyncio.run(scenario())


def test_oauth_routes_rotate_cookie_and_clean_callback_url(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness, provider = Harness(tmp_path / "player.sqlite3"), Provider()
        app = create_app(
            harness.runtime,
            auth_settings=harness.auth_settings,
            identity_provider=provider,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
            ) as client,
        ):
            begin = await client.get(
                "/api/auth/discord", headers={"X-Forwarded-Host": "evil.invalid"}
            )
            assert begin.status_code == 303
            assert (
                "HttpOnly" in begin.headers["set-cookie"]
                and "SameSite=lax" in begin.headers["set-cookie"]
            )
            state = parse_qs(urlsplit(begin.headers["location"]).query)["state"][0]
            callback = await client.get(
                "/api/auth/discord/callback",
                params={"state": state, "code": "single-use-code"},
            )
            assert callback.headers["location"] == "/"
            assert len(callback.headers.get_list("set-cookie")) == 2
            assert (
                SESSION_COOKIE in client.cookies and LOGIN_COOKIE not in client.cookies
            )
            assert (await client.get("/api/auth/session")).status_code == 200
            replay = await client.get(
                "/api/auth/discord/callback",
                params={"state": state, "code": "single-use-code"},
            )
            assert replay.headers["location"] == "/login?error=login_expired"
            assert provider.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["logout", "expire", "revoke"])
def test_idle_sse_closes_within_five_seconds_after_access_is_lost(
    tmp_path: Path, change: str
) -> None:
    async def scenario() -> None:
        harness = Harness(tmp_path / "player.sqlite3")
        shutdown = asyncio.Event()
        async with (
            running_server(
                create_app(
                    harness.runtime,
                    shutdown_event=shutdown,
                    auth_settings=harness.auth_settings,
                ),
                shutdown,
            ) as (url, _, _),
            httpx.AsyncClient(
                base_url=url, cookies={SESSION_COOKIE: TEST_TOKEN}, headers=AUTH_HEADERS
            ) as client,
        ):
            async with client.stream("GET", "/api/events") as stream:
                lines = stream.aiter_lines()
                await next_state(lines)
                started = time.monotonic()
                if change == "revoke":
                    harness.auth_settings.access_path.write_text(
                        "discord_ids = []", encoding="utf-8"
                    )
                elif change == "logout":
                    assert (await client.post("/api/auth/logout")).status_code == 204
                else:
                    engine = database_engine(harness.path)
                    try:
                        with Session(engine) as db, db.begin():
                            db.execute(update(SessionRow).values(expires_at=0))
                    finally:
                        engine.dispose()
                async with asyncio.timeout(5):
                    events = [line async for line in lines]
                assert "event: auth" in events
                assert not any(line.startswith("event: state") for line in events)
                assert time.monotonic() - started < 5
                assert (await client.get("/api/state")).status_code in (401, 403)

    asyncio.run(scenario())

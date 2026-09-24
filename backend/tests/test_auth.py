# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nahormaar_backend.application.auth import (
    SESSION_SECONDS,
    Auth,
    csrf_token,
    digest,
)
from nahormaar_backend.application.access import Access, Operators
from nahormaar_backend.config import AuthSettings, ConfigurationError
from nahormaar_backend.domain.access import AccessRole
from nahormaar_backend.domain.identity import AuthError, DiscordIdentity
from nahormaar_backend.integrations.discord_oauth import DiscordOAuth
from nahormaar_backend.persistence.database import database_engine
from nahormaar_backend.persistence.models import LoginRow, SessionRow
from nahormaar_backend.engine.schema import upgrade
from engine.database import database_url


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
    path = tmp_path / "auth.db"
    database = database_url(path)
    engine = database_engine(database)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    access_path = tmp_path / "access.toml"
    access_path.write_text('owner_id = "9"\nadmin_ids = ["8"]', encoding="utf-8")
    access = Access(database, Operators("9", ("8",)))
    access.accounts.grant_access("1", "9", datetime.now(UTC))
    provider, clock = Provider(), [time.time()]
    settings = AuthSettings(
        "https://music.example.test",
        "123",
        "test-secret",
        database,
        access_path,
    )
    return (
        Auth(
            settings,
            ("0002", "0118"),
            access=access,
            provider=provider,
            clock=lambda: clock[0],
        ),
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
                assert user.account.role is AccessRole.USER
                assert user.expires_at == clock[0] + SESSION_SECONDS
                assert user.csrf == csrf_token(tokens[0])
                assert not user.account.profile_complete
                engine = database_engine(auth.settings.database_url)
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
            auth.settings,
            auth.avatars,
            access=Access(auth.settings.database_url, Operators("9", ("8",))),
            provider=provider,
            clock=lambda: clock[0],
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
            await auth.access.revoke("9", "1")
            with pytest.raises(AuthError, match="signed_out"):
                await auth.authenticate(fresh)
            await auth.access.grant("9", "1")
            token = await login(auth)
            clock[0] += SESSION_SECONDS
            with pytest.raises(AuthError, match="signed_out"):
                await auth.authenticate(token)
        finally:
            await auth.close()

    asyncio.run(scenario())


def test_operator_roles_are_loaded_from_configuration(tmp_path: Path) -> None:
    async def scenario() -> None:
        auth, provider, _ = auth_service(tmp_path)
        try:
            listener = await login(auth)
            assert not (await auth.authenticate(listener)).admin
            assert (await auth.access.require("9")).value == "owner"
            assert (await auth.access.require("8")).value == "admin"
            provider.user = DiscordIdentity("9", "Owner")
            owner = await auth.authenticate(await login(auth))
            assert owner.account.role is AccessRole.OWNER and owner.admin
            provider.user = DiscordIdentity("8", "Admin")
            admin = await auth.authenticate(await login(auth))
            assert admin.account.role is AccessRole.ADMIN and admin.admin
        finally:
            await auth.close()

    asyncio.run(scenario())


def test_discord_oauth_uses_identify_pkce_fixed_redirect_and_discards_tokens(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []
    settings = AuthSettings(
        "https://music.example.test",
        "123",
        "test-secret",
        database_url(tmp_path / "db"),
        tmp_path / "access",
    )
    verifier = secrets.token_urlsafe(48)

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/token"):
            body = parse_qs(request.content.decode())
            assert body["code_verifier"] == [verifier]
            assert body["redirect_uri"] == [settings.redirect_uri]
            assert body["grant_type"] == ["authorization_code"]
            return httpx2.Response(
                200,
                json={
                    "access_token": "temporary",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        assert request.url.path == "/api/v10/users/@me"
        assert request.headers["authorization"] == "Bearer temporary"
        return httpx2.Response(
            200, json={"id": "1", "username": "Account", "global_name": "Custom"}
        )

    async def scenario() -> None:
        provider = DiscordOAuth(settings, transport=httpx2.MockTransport(respond))
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
        AuthSettings(
            origin,
            "123",
            "test-secret",
            database_url(tmp_path / "db"),
            tmp_path / "access",
        )

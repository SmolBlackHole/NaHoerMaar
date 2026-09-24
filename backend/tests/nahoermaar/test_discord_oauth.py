# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from nahoermaar.config import AuthSettings
from nahoermaar.integrations.discord_oauth import DiscordOAuth
from nahoermaar.users.domain import AuthError, AuthErrorCode


def test_discord_oauth_uses_fixed_redirect_pkce_and_returns_identity() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/oauth2/token":
            return httpx2.Response(
                200, json={"access_token": "secret", "token_type": "Bearer"}
            )
        return httpx2.Response(
            200,
            json={
                "id": "7",
                "username": "discord-user",
                "global_name": "Display name",
                "avatar": "avatar-hash",
            },
        )

    settings = AuthSettings(
        "https://music.example.test",
        "1550894913980465212",
        "client-secret",
        Path("access.toml"),
    )
    provider = DiscordOAuth(settings, transport=httpx2.MockTransport(respond))

    async def scenario() -> None:
        url = await provider.authorization_url("state", "v" * 64)
        query = parse_qs(urlsplit(url).query)
        assert query["redirect_uri"] == [settings.redirect_uri]
        assert query["code_challenge_method"] == ["S256"]
        identity = await provider.identity("oauth-code", "v" * 64)
        assert identity.discord_id == "7"
        assert identity.username == "Display name"
        assert identity.avatar_hash == "avatar-hash"

    asyncio.run(scenario())
    assert [request.url.path for request in requests] == [
        "/api/oauth2/token",
        "/api/v10/users/@me",
    ]


def test_discord_oauth_fails_closed_without_credentials() -> None:
    provider = DiscordOAuth(
        AuthSettings("http://localhost:3000", "", "", Path("access.toml"))
    )

    with pytest.raises(AuthError) as error:
        asyncio.run(provider.authorization_url("state", "v" * 64))

    assert error.value.code is AuthErrorCode.LOGIN_UNAVAILABLE

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord OAuth exchange through Authlib and HTTPX."""

from types import TracebackType
from typing import Protocol, Self, cast

import httpx
from authlib.integrations.httpx_client import (  # type: ignore[import-untyped]
    AsyncOAuth2Client,
)
from authlib.oauth2 import OAuth2Error  # type: ignore[import-untyped]

from ..config import AuthSettings
from ..domain.identity import AuthError, DiscordIdentity, discord_id


class OAuthClient(Protocol):
    """Typed boundary for Authlib's untyped HTTPX integration."""

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def create_authorization_url(
        self, url: str, *, state: str, code_verifier: str
    ) -> tuple[str, str]: ...
    async def fetch_token(
        self, url: str, *, code: str, code_verifier: str
    ) -> object: ...
    async def get(self, url: str) -> httpx.Response: ...


class DiscordOAuth:
    def __init__(
        self,
        settings: AuthSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings, self.transport = settings, transport

    def _client(self) -> OAuthClient:
        if not self.settings.client_id or not self.settings.client_secret:
            raise AuthError("login_unavailable", 503)
        return cast(
            OAuthClient,
            AsyncOAuth2Client(
                self.settings.client_id,
                self.settings.client_secret,
                scope="identify",
                redirect_uri=self.settings.redirect_uri,
                code_challenge_method="S256",
                token_endpoint_auth_method="client_secret_post",  # noqa: S106 - protocol name
                timeout=15,
                transport=self.transport,
                follow_redirects=False,
            ),
        )

    async def authorization_url(self, state: str, verifier: str) -> str:
        async with self._client() as client:
            url, _ = client.create_authorization_url(
                "https://discord.com/oauth2/authorize",
                state=state,
                code_verifier=verifier,
            )
            return url

    async def identity(self, code: str, verifier: str) -> DiscordIdentity:
        try:
            async with self._client() as client:
                await client.fetch_token(
                    "https://discord.com/api/oauth2/token",
                    code=code,
                    code_verifier=verifier,
                )
                response = await client.get("https://discord.com/api/v10/users/@me")
                response.raise_for_status()
                data = cast(dict[str, object], response.json())
                identifier = data.get("id")
                name = data.get("global_name") or data.get("username")
                if (
                    not discord_id(identifier)
                    or not isinstance(name, str)
                    or not name.strip()
                ):
                    raise AuthError("login_failed")
                return DiscordIdentity(cast(str, identifier), name.strip()[:32])
        except (httpx.HTTPError, OAuth2Error, ValueError, AttributeError) as exc:
            # Never include token responses or OAuth codes in errors or logs.
            raise AuthError("login_failed", 502) from exc

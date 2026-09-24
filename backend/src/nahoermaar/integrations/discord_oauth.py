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

from nahoermaar.config import AuthSettings
from nahoermaar.users.domain import AuthError, AuthErrorCode
from nahoermaar.users.service import ProvidedDiscordIdentity


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
    """Resolve one Discord user identity with OAuth2 and PKCE."""

    __slots__ = ("_settings", "_transport")

    def __init__(
        self,
        settings: AuthSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _client(self) -> OAuthClient:
        if not self._settings.client_id or not self._settings.client_secret:
            raise AuthError(AuthErrorCode.LOGIN_UNAVAILABLE, 503)
        return cast(
            OAuthClient,
            AsyncOAuth2Client(
                self._settings.client_id,
                self._settings.client_secret,
                scope="identify",
                redirect_uri=self._settings.redirect_uri,
                code_challenge_method="S256",
                token_endpoint_auth_method="client_secret_post",  # noqa: S106
                timeout=15,
                transport=self._transport,
                follow_redirects=False,
            ),
        )

    async def authorization_url(self, state: str, verifier: str) -> str:
        try:
            async with self._client() as client:
                url, _ = client.create_authorization_url(
                    "https://discord.com/oauth2/authorize",
                    state=state,
                    code_verifier=verifier,
                )
                return url
        except (httpx.HTTPError, OAuth2Error, ValueError, AttributeError) as error:
            raise AuthError(AuthErrorCode.LOGIN_FAILED, 502) from error

    async def identity(self, code: str, verifier: str) -> ProvidedDiscordIdentity:
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
                discord_id = data.get("id")
                username = data.get("global_name") or data.get("username")
                avatar = data.get("avatar")
                if (
                    not isinstance(discord_id, str)
                    or not isinstance(username, str)
                    or not username.strip()
                    or (avatar is not None and not isinstance(avatar, str))
                ):
                    raise AuthError(AuthErrorCode.LOGIN_FAILED, 502)
                return ProvidedDiscordIdentity(
                    discord_id,
                    username.strip()[:32],
                    avatar,
                )
        except AuthError:
            raise
        except (httpx.HTTPError, OAuth2Error, ValueError, AttributeError) as error:
            # Never include token responses or OAuth codes in errors or logs.
            raise AuthError(AuthErrorCode.LOGIN_FAILED, 502) from error

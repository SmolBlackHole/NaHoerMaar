# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord identity, revocable local sessions, and the live access list."""

import asyncio
import hashlib
import re
import secrets
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Self, cast
from types import TracebackType
from urllib.parse import urlsplit

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client  # type: ignore[import-untyped]
from authlib.oauth2 import OAuth2Error  # type: ignore[import-untyped]

from .accounts import Account, Accounts
from .config import ConfigurationError, environment_values

SESSION_SECONDS = 7 * 24 * 60 * 60
SESSION_COOKIE = "nahormaar_session"
LOGIN_COOKIE = "nahormaar_login"
ACCESS_CHECK_SECONDS = 2.0
CALLBACK_PATH = "/api/auth/discord/callback"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(session_token: str) -> str:
    return digest("csrf:" + session_token)


def discord_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
        and int(value) < 2**64
    )


@dataclass(frozen=True, slots=True)
class AuthSettings:
    public_origin: str
    client_id: str
    client_secret: str = field(repr=False)
    database_path: Path
    access_path: Path

    def __post_init__(self) -> None:
        url = urlsplit(self.public_origin)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or self.public_origin != f"{url.scheme}://{url.netloc}"
            or (
                url.scheme == "http"
                and url.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ConfigurationError(
                "PUBLIC_ORIGIN must be an HTTPS origin (HTTP is allowed on loopback only)."
            )
        if self.client_id and not discord_id(self.client_id):
            raise ConfigurationError(
                "DISCORD_CLIENT_ID must be a Discord application ID."
            )

    @property
    def secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return self.public_origin + CALLBACK_PATH

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthSettings":
        values = environment_values(environ)
        origin = (
            values.get("PUBLIC_ORIGIN", "http://localhost:3012").strip().rstrip("/")
        )
        return cls(
            origin,
            values.get("DISCORD_CLIENT_ID", "").strip(),
            values.get("DISCORD_CLIENT_SECRET", "").strip(),
            Path(values.get("DATABASE_PATH") or "data/player.sqlite3").resolve(),
            Path(values.get("ACCESS_PATH") or "access.toml").resolve(),
        )


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    id: str
    name: str


class IdentityProvider(Protocol):
    async def authorization_url(self, state: str, verifier: str) -> str: ...
    async def identity(self, code: str, verifier: str) -> DiscordIdentity: ...


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


@dataclass(frozen=True, slots=True)
class Authenticated:
    account: Account
    expires_at: float
    csrf: str = field(repr=False)


class Auth:
    def __init__(
        self,
        settings: AuthSettings,
        avatars: tuple[str, ...],
        *,
        provider: IdentityProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.accounts = Accounts(settings.database_path)
        self.avatars = avatars
        self.provider = provider or DiscordOAuth(settings)
        self.clock = clock

    async def close(self) -> None:
        await asyncio.to_thread(self.accounts.close)

    def _check_access(self, identifier: str) -> None:
        try:
            data = tomllib.loads(
                self.settings.access_path.read_text(encoding="utf-8-sig")
            )
            identifiers = data.get("discord_ids")
            if (
                set(data) != {"discord_ids"}
                or not isinstance(identifiers, list)
                or not all(discord_id(item) for item in cast(list[object], identifiers))
            ):
                raise ValueError("Invalid access list.")
        except (OSError, ValueError) as exc:
            raise AuthError("access_unavailable", 503) from exc
        if identifier not in identifiers:
            raise AuthError("access_denied", 403)

    async def begin(self, browser_token: str | None) -> tuple[str, str]:
        browser = (
            browser_token
            if browser_token and re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_token)
            else secrets.token_urlsafe(32)
        )
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        url = await self.provider.authorization_url(state, verifier)
        if not await asyncio.to_thread(
            self.accounts.begin_login,
            digest(state),
            digest(browser),
            verifier,
            self.clock(),
        ):
            raise AuthError("login_busy", 429)
        return url, browser

    async def callback(
        self,
        state: str | None,
        browser: str | None,
        code: str | None,
        error: str | None,
        previous: str | None,
    ) -> str:
        if not state or not browser or len(state) > 128 or len(browser) > 128:
            raise AuthError("login_expired")
        verifier = await asyncio.to_thread(
            self.accounts.consume_login, digest(state), digest(browser), self.clock()
        )
        if verifier is None:
            raise AuthError("login_expired")
        if error:
            raise AuthError(
                "login_cancelled" if error == "access_denied" else "login_failed"
            )
        if not code or len(code) > 2048:
            raise AuthError("login_failed")
        identity = await self.provider.identity(code, verifier)
        await asyncio.to_thread(self._check_access, identity.id)
        token, now = secrets.token_urlsafe(32), self.clock()
        await asyncio.to_thread(
            self.accounts.create_session,
            identity.id,
            identity.name,
            secrets.choice(self.avatars),
            digest(token),
            now + SESSION_SECONDS,
            now,
            digest(previous) if previous else None,
        )
        return token

    async def authenticate(
        self, token: str | None, *, check_access: bool = True
    ) -> Authenticated:
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthError("signed_out")
        result = await asyncio.to_thread(
            self.accounts.session, digest(token), self.clock()
        )
        if result is None:
            raise AuthError("signed_out")
        account, expires_at = result
        if check_access:
            try:
                await asyncio.to_thread(self._check_access, account.discord_id)
            except AuthError as exc:
                if exc.code == "access_denied":
                    await asyncio.to_thread(self.accounts.revoke, account.profile.id)
                raise
        return Authenticated(account, expires_at, csrf_token(token))

    async def logout(self, token: str) -> None:
        await asyncio.to_thread(self.accounts.logout, digest(token))

    async def profile(self, user: Authenticated, name: str, avatar: str) -> Account:
        if avatar not in self.avatars:
            raise AuthError("invalid_avatar", 422)
        return await asyncio.to_thread(
            self.accounts.update_profile, user.account.profile.id, name, avatar
        )

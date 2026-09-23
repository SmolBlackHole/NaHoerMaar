# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord identity, revocable local sessions, and the live access list."""

import asyncio
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from ..config import AuthSettings
from ..domain.accounts import Account
from ..domain.identity import AuthError, DiscordIdentity
from ..domain.preferences import Appearance
from .access import Access

SESSION_SECONDS = 7 * 24 * 60 * 60
SESSION_COOKIE = "nahormaar_session"
LOGIN_COOKIE = "nahormaar_login"
ACCESS_CHECK_SECONDS = 2.0
_LOGGER = logging.getLogger(__name__)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(session_token: str) -> str:
    return digest("csrf:" + session_token)


class IdentityProvider(Protocol):
    async def authorization_url(self, state: str, verifier: str) -> str: ...
    async def identity(self, code: str, verifier: str) -> DiscordIdentity: ...


@dataclass(frozen=True, slots=True)
class Authenticated:
    account: Account
    expires_at: float
    csrf: str = field(repr=False)

    @property
    def admin(self) -> bool:
        return self.account.role is not None and self.account.role.privileged


class Auth:
    def __init__(
        self,
        settings: AuthSettings,
        avatars: tuple[str, ...],
        *,
        access: Access,
        provider: IdentityProvider,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.access = access
        self.accounts = access.accounts
        self.avatars = avatars
        self.provider = provider
        self.clock = clock

    async def close(self) -> None:
        await self.access.close()

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
        _LOGGER.info("auth.login_started")
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
        role = await self.access.require(identity.id)
        token, now = secrets.token_urlsafe(32), self.clock()
        account = await asyncio.to_thread(
            self.accounts.create_session,
            identity.id,
            identity.name,
            secrets.choice(self.avatars),
            role,
            digest(token),
            now + SESSION_SECONDS,
            now,
            digest(previous) if previous else None,
        )
        if account is None:
            raise AuthError("access_denied", 403)
        _LOGGER.info("auth.login_completed account=%s", identity.id)
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
        role = account.role
        if check_access:
            try:
                role = await self.access.require(account.discord_id)
            except AuthError as exc:
                if exc.code == "access_denied":
                    await asyncio.to_thread(self.accounts.revoke, account.profile.id)
                    _LOGGER.warning(
                        "auth.session_revoked account=%s reason=access_denied",
                        account.discord_id,
                    )
                raise
        return Authenticated(replace(account, role=role), expires_at, csrf_token(token))

    async def logout(self, token: str) -> None:
        await asyncio.to_thread(self.accounts.logout, digest(token))
        _LOGGER.info("auth.session_logged_out")

    async def account(self, discord_id: str) -> Account:
        account = await asyncio.to_thread(self.accounts.account, discord_id)
        if account is None:
            raise AuthError("profile_not_found", 404)
        role = await self.access.role(discord_id)
        if role is None:
            raise AuthError("profile_not_found", 404)
        return replace(account, role=role)

    async def profile(self, user: Authenticated, name: str, avatar: str) -> Account:
        if avatar not in self.avatars:
            raise AuthError("invalid_avatar", 422)
        account = await asyncio.to_thread(
            self.accounts.update_profile, user.account.profile.id, name, avatar
        )
        _LOGGER.info("auth.profile_updated account=%s", user.account.discord_id)
        return replace(account, role=user.account.role)

    async def appearance(self, user: Authenticated, value: Appearance) -> Appearance:
        appearance = await asyncio.to_thread(
            self.accounts.update_appearance, user.account.profile.id, value
        )
        _LOGGER.info("auth.appearance_updated account=%s", user.account.discord_id)
        return appearance

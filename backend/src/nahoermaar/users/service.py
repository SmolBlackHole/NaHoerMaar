# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authentication, profile and access use cases for the User aggregate."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.messaging import Command, Event

from .domain import (
    AccessAction,
    AccessEvent,
    AccessRole,
    Appearance,
    Authenticated,
    AuthError,
    AuthErrorCode,
    BrowserSession,
    DiscordIdentity,
    LoginAttempt,
    User,
    UserId,
    UserProfile,
)
from .repository import AuthRepository, UserRepository

SESSION_COOKIE = "nahormaar_session"
LOGIN_COOKIE = "nahormaar_login"
SESSION_LIFETIME = timedelta(days=7)
LOGIN_LIFETIME = timedelta(minutes=10)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_LOGGER = logging.getLogger(__name__)

type Clock = Callable[[], datetime]
type UnitOfWorkFactory = Callable[[], UnitOfWork]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> bytes:
    """Return the fixed-size value stored for a browser secret."""
    return hashlib.sha256(value.encode()).digest()


def csrf_token(session_token: str) -> str:
    """Derive the non-cookie CSRF proof for one authenticated browser."""
    return hashlib.sha256(("csrf:" + session_token).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProvidedDiscordIdentity:
    """Discord-owned fields returned by the OAuth provider."""

    discord_id: str
    username: str
    avatar_hash: str | None


class IdentityProvider(Protocol):
    async def authorization_url(self, state: str, verifier: str) -> str: ...

    async def identity(self, code: str, verifier: str) -> ProvidedDiscordIdentity: ...


@dataclass(frozen=True, slots=True)
class Operators:
    """Owner and admins controlled only by the host configuration."""

    owner_id: str
    admin_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_operator_id(self.owner_id)
        if len(set(self.admin_ids)) != len(self.admin_ids):
            raise ValueError("access.toml contains duplicate admin IDs.")
        if self.owner_id in self.admin_ids:
            raise ValueError("The owner cannot also be listed as an admin.")
        for admin_id in self.admin_ids:
            _validate_operator_id(admin_id)

    @classmethod
    def load(cls, path: Path) -> Operators:
        """Load a fail-closed operator configuration."""
        try:
            values = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise AuthError(AuthErrorCode.ACCESS_UNAVAILABLE, 503) from error
        if set(values) - {"owner_id", "admin_ids"}:
            raise AuthError(AuthErrorCode.ACCESS_UNAVAILABLE, 503)
        owner_id = values.get("owner_id")
        raw_admin_ids = values.get("admin_ids", [])
        if not isinstance(owner_id, str) or not isinstance(raw_admin_ids, list):
            raise AuthError(AuthErrorCode.ACCESS_UNAVAILABLE, 503)
        admin_ids: list[str] = []
        for value in cast(list[object], raw_admin_ids):
            if not isinstance(value, str):
                raise AuthError(AuthErrorCode.ACCESS_UNAVAILABLE, 503)
            admin_ids.append(value)
        try:
            return cls(owner_id, tuple(admin_ids))
        except ValueError as error:
            raise AuthError(AuthErrorCode.ACCESS_UNAVAILABLE, 503) from error

    def role_for(self, discord_id: str) -> AccessRole | None:
        if discord_id == self.owner_id:
            return AccessRole.OWNER
        if discord_id in self.admin_ids:
            return AccessRole.ADMIN
        return None

    @property
    def roles(self) -> dict[str, AccessRole]:
        """Return the exact privileged role assignment."""
        return {self.owner_id: AccessRole.OWNER} | {
            discord_id: AccessRole.ADMIN for discord_id in self.admin_ids
        }


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    browser_token: str = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class LoginCompletion:
    session_token: str = dataclass_field(repr=False)
    user: User


@dataclass(frozen=True, slots=True)
class BeginLogin(Command[LoginStart]):
    browser_token: str | None = dataclass_field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CompleteLogin(Command[LoginCompletion]):
    state: str | None = dataclass_field(repr=False)
    browser_token: str | None = dataclass_field(repr=False)
    code: str | None = dataclass_field(repr=False)
    error: str | None
    previous_session: str | None = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class Logout(Command[None]):
    session_token: str | None = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class ReconcileOperators(Command[tuple[AccessEvent, ...]]):
    pass


@dataclass(frozen=True, slots=True)
class GrantAccess(Command[AccessEvent | None]):
    actor_id: UserId
    discord_id: str


@dataclass(frozen=True, slots=True)
class RevokeAccess(Command[AccessEvent | None]):
    actor_id: UserId
    discord_id: str


@dataclass(frozen=True, slots=True)
class SaveProfile(Command[User]):
    user_id: UserId
    profile: UserProfile


@dataclass(frozen=True, slots=True)
class SaveAppearance(Command[User]):
    user_id: UserId
    appearance: Appearance


@dataclass(frozen=True, slots=True)
class UserLoggedIn(Event):
    user_id: UserId


@dataclass(frozen=True, slots=True)
class UserAccessChanged(Event):
    change: AccessEvent


@dataclass(frozen=True, slots=True)
class UserProfileChanged(Event):
    user_id: UserId


class AccessService:
    """Apply the access policy while keeping transactions explicit."""

    __slots__ = ("_clock", "_operators", "_units")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        operators: Operators,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._operators = operators
        self._clock = clock

    @property
    def operators(self) -> Operators:
        return self._operators

    async def reconcile(self) -> tuple[AccessEvent, ...]:
        """Make privileged database roles exactly match access.toml."""
        now = self._clock()
        changes: list[AccessEvent] = []
        desired = self._operators.roles
        async with self._units() as work:
            users = UserRepository(work.session)
            persisted_operators = await users.privileged()
            handled: set[str] = set()

            for user in persisted_operators:
                discord_id = user.discord.discord_id
                role = desired.get(discord_id)
                handled.add(discord_id)
                if user.role is role:
                    continue
                updated = replace(
                    user,
                    role=role,
                    access_granted_by=None,
                    access_granted_at=None,
                    updated_at=now,
                )
                await users.update(updated)
                changes.append(_access_event(user, updated, None, now))

            for discord_id, role in desired.items():
                if discord_id in handled:
                    continue
                configured_user = await users.get_by_discord_id(
                    discord_id, for_update=True
                )
                if configured_user is None:
                    configured_user = _new_user(discord_id, now, role=role)
                    users.add(configured_user)
                    changes.append(
                        AccessEvent(
                            uuid4(),
                            configured_user.id,
                            None,
                            AccessAction.OPERATOR_ROLE_CHANGED,
                            None,
                            role,
                            now,
                        )
                    )
                    continue
                if configured_user.role is role:
                    continue
                updated = replace(
                    configured_user,
                    role=role,
                    access_granted_by=None,
                    access_granted_at=None,
                    updated_at=now,
                )
                await users.update(updated)
                changes.append(_access_event(configured_user, updated, None, now))

            for change in changes:
                await users.add_access_event(change)
            await work.commit()

        _LOGGER.info(
            "access.operators_reconciled owner=%s admins=%d changes=%d",
            self._operators.owner_id,
            len(self._operators.admin_ids),
            len(changes),
        )
        return tuple(changes)

    async def require_access(self, user_id: UserId) -> User:
        async with self._units() as work:
            user = await UserRepository(work.session).get(user_id)
        if user is None or not user.has_access:
            _LOGGER.info("access.denied user_id=%s requirement=access", user_id)
            raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
        _LOGGER.debug(
            "access.allowed user_id=%s requirement=access role=%s",
            user.id,
            user.role.value if user.role is not None else None,
        )
        return user

    async def require_admin(self, user_id: UserId) -> User:
        user = await self.require_access(user_id)
        if user.role is None or not user.role.privileged:
            _LOGGER.info("access.denied user_id=%s requirement=admin", user_id)
            raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
        _LOGGER.debug(
            "access.allowed user_id=%s requirement=admin role=%s",
            user.id,
            user.role.value,
        )
        return user

    async def grants(self) -> tuple[User, ...]:
        async with self._units() as work:
            return await UserRepository(work.session).grants()

    async def history(self, limit: int = 100) -> tuple[AccessEvent, ...]:
        async with self._units() as work:
            return await UserRepository(work.session).access_history(limit)

    async def grant(self, actor_id: UserId, discord_id: str) -> AccessEvent | None:
        _validate_discord_id(discord_id)
        if self._operators.role_for(discord_id) is not None:
            raise AuthError(AuthErrorCode.OPERATOR_ACCESS_MANAGED_IN_CONFIG, 409)
        now = self._clock()
        async with self._units() as work:
            users = UserRepository(work.session)
            actor = await users.get(actor_id)
            if actor is None or actor.role is None or not actor.role.privileged:
                raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
            subject = await users.get_by_discord_id(discord_id, for_update=True)
            if subject is not None and subject.role is AccessRole.USER:
                _LOGGER.debug(
                    "access.grant_unchanged actor_id=%s subject_id=%s",
                    actor.id,
                    subject.id,
                )
                return None
            if subject is not None and subject.role is not None:
                raise AuthError(AuthErrorCode.OPERATOR_ACCESS_MANAGED_IN_CONFIG, 409)
            if subject is None:
                subject = _new_user(
                    discord_id,
                    now,
                    role=AccessRole.USER,
                    granted_by=actor.id,
                )
                users.add(subject)
                before = None
            else:
                before = subject.role
                subject = replace(
                    subject,
                    role=AccessRole.USER,
                    access_granted_by=actor.id,
                    access_granted_at=now,
                    updated_at=now,
                )
                await users.update(subject)
            change = AccessEvent(
                uuid4(),
                subject.id,
                actor.id,
                AccessAction.GRANTED,
                before,
                AccessRole.USER,
                now,
            )
            await users.add_access_event(change)
            await work.commit()
        _LOGGER.info("access.granted actor_id=%s subject_id=%s", actor.id, subject.id)
        return change

    async def revoke(self, actor_id: UserId, discord_id: str) -> AccessEvent | None:
        _validate_discord_id(discord_id)
        if self._operators.role_for(discord_id) is not None:
            raise AuthError(AuthErrorCode.OPERATOR_ACCESS_MANAGED_IN_CONFIG, 409)
        now = self._clock()
        async with self._units() as work:
            users = UserRepository(work.session)
            actor = await users.get(actor_id)
            if actor is None or actor.role is None or not actor.role.privileged:
                raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
            subject = await users.get_by_discord_id(discord_id, for_update=True)
            if subject is None or subject.role is not AccessRole.USER:
                _LOGGER.debug(
                    "access.revoke_unchanged actor_id=%s discord_id=%s",
                    actor.id,
                    discord_id,
                )
                return None
            if (
                actor.role is not AccessRole.OWNER
                and subject.access_granted_by != actor.id
            ):
                raise AuthError(AuthErrorCode.GRANT_NOT_OWNED, 403)
            updated = replace(
                subject,
                role=None,
                access_granted_by=None,
                access_granted_at=None,
                updated_at=now,
            )
            await users.update(updated)
            await AuthRepository(work.session).delete_user_sessions(subject.id)
            change = AccessEvent(
                uuid4(),
                subject.id,
                actor.id,
                AccessAction.REVOKED,
                AccessRole.USER,
                None,
                now,
            )
            await users.add_access_event(change)
            await work.commit()
        _LOGGER.info("access.revoked actor_id=%s subject_id=%s", actor.id, subject.id)
        return change


class AuthService:
    """Manage Discord login and revocable browser sessions."""

    __slots__ = ("_clock", "_provider", "_units")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        provider: IdentityProvider,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._provider = provider
        self._clock = clock

    async def begin(self, browser_token: str | None) -> LoginStart:
        if browser_token is not None and _TOKEN.fullmatch(browser_token):
            browser = browser_token
            reused_browser = True
        else:
            browser = secrets.token_urlsafe(32)
            reused_browser = False
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        authorization_url = await self._provider.authorization_url(state, verifier)
        now = self._clock()
        attempt = LoginAttempt(
            digest(state),
            digest(browser),
            verifier,
            now,
            now + LOGIN_LIFETIME,
        )
        async with self._units() as work:
            accepted = await AuthRepository(work.session).begin_login(attempt)
            if not accepted:
                raise AuthError(AuthErrorCode.LOGIN_BUSY, 429)
            await work.commit()
        _LOGGER.info("auth.login_started browser_reused=%s", reused_browser)
        return LoginStart(authorization_url, browser)

    async def complete(
        self,
        *,
        state: str | None,
        browser_token: str | None,
        code: str | None,
        error: str | None,
        previous_session: str | None,
    ) -> LoginCompletion:
        if (
            state is None
            or browser_token is None
            or len(state) > 128
            or len(browser_token) > 128
        ):
            raise AuthError(AuthErrorCode.LOGIN_EXPIRED)
        now = self._clock()
        async with self._units() as work:
            verifier = await AuthRepository(work.session).consume_login(
                digest(state), digest(browser_token), now
            )
            await work.commit()
        if verifier is None:
            raise AuthError(AuthErrorCode.LOGIN_EXPIRED)
        if error is not None:
            if error == "access_denied":
                raise AuthError(AuthErrorCode.LOGIN_CANCELLED)
            raise AuthError(AuthErrorCode.LOGIN_FAILED)
        if code is None or not code or len(code) > 2048:
            raise AuthError(AuthErrorCode.LOGIN_FAILED)

        provided = await self._provider.identity(code, verifier)
        try:
            discord = DiscordIdentity(
                provided.discord_id,
                provided.username.strip()[:32],
                provided.avatar_hash,
                now,
            )
        except ValueError as validation_error:
            raise AuthError(AuthErrorCode.LOGIN_FAILED, 502) from validation_error

        session_token = secrets.token_urlsafe(32)
        async with self._units() as work:
            users = UserRepository(work.session)
            user = await users.get_by_discord_id(discord.discord_id, for_update=True)
            if user is None or not user.has_access:
                raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
            user = replace(
                user,
                discord=discord,
                last_login_at=now,
                updated_at=now,
            )
            await users.update(user)
            await AuthRepository(work.session).create_session(
                BrowserSession(
                    digest(session_token),
                    user.id,
                    now,
                    now + SESSION_LIFETIME,
                ),
                previous_hash=(
                    digest(previous_session)
                    if previous_session is not None
                    and _TOKEN.fullmatch(previous_session)
                    else None
                ),
            )
            await work.commit()
        _LOGGER.info(
            "auth.login_completed user_id=%s role=%s previous_session=%s",
            user.id,
            user.role.value if user.role is not None else None,
            previous_session is not None,
        )
        return LoginCompletion(session_token, user)

    async def authenticate(self, session_token: str | None) -> Authenticated:
        if session_token is None or _TOKEN.fullmatch(session_token) is None:
            _LOGGER.debug("auth.session_rejected reason=missing_or_invalid_token")
            raise AuthError(AuthErrorCode.SIGNED_OUT, 401)
        now = self._clock()
        token_hash = digest(session_token)
        async with self._units() as work:
            auth = AuthRepository(work.session)
            session = await auth.session(token_hash, now)
            if session is None:
                _LOGGER.debug("auth.session_rejected reason=not_found_or_expired")
                raise AuthError(AuthErrorCode.SIGNED_OUT, 401)
            user = await UserRepository(work.session).get(session.user_id)
            if user is None or not user.has_access:
                await auth.delete_session(token_hash)
                await work.commit()
                _LOGGER.info(
                    "auth.session_revoked user_id=%s reason=access_missing",
                    session.user_id,
                )
                raise AuthError(AuthErrorCode.ACCESS_DENIED, 403)
        _LOGGER.debug(
            "auth.session_authenticated user_id=%s expires_at=%s",
            user.id,
            session.expires_at.isoformat(),
        )
        return Authenticated(user, session.expires_at, csrf_token(session_token))

    async def logout(self, session_token: str | None) -> None:
        if session_token is None or _TOKEN.fullmatch(session_token) is None:
            return
        async with self._units() as work:
            await AuthRepository(work.session).delete_session(digest(session_token))
            await work.commit()
        _LOGGER.info("auth.logout_completed session_present=true")

    async def profile(self, user_id: UserId) -> User:
        async with self._units() as work:
            user = await UserRepository(work.session).get(user_id)
        if user is None or not user.has_access:
            raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
        return user

    async def save_profile(self, user_id: UserId, profile: UserProfile) -> User:
        now = self._clock()
        async with self._units() as work:
            users = UserRepository(work.session)
            user = await users.get(user_id)
            if user is None:
                raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
            user = replace(user, profile=profile, updated_at=now)
            await users.update(user)
            await work.commit()
        _LOGGER.info(
            "profile.saved user_id=%s complete=%s",
            user.id,
            user.profile.complete,
        )
        return user

    async def save_appearance(self, user_id: UserId, appearance: Appearance) -> User:
        now = self._clock()
        async with self._units() as work:
            users = UserRepository(work.session)
            user = await users.get(user_id)
            if user is None:
                raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
            user = replace(user, appearance=appearance, updated_at=now)
            await users.update(user)
            await work.commit()
        _LOGGER.info(
            "appearance.saved user_id=%s mode=%s primary=%s neutral=%s "
            "font=%s icons=%s text_size=%s artwork_colors=%s",
            user.id,
            appearance.mode.value,
            appearance.primary_color.value,
            appearance.neutral_color.value,
            appearance.font_family.value,
            appearance.icon_set.value,
            appearance.text_size.value,
            appearance.artwork_colors,
        )
        return user


def _new_user(
    discord_id: str,
    now: datetime,
    *,
    role: AccessRole | None = None,
    granted_by: UserId | None = None,
) -> User:
    return User(
        id=UserId(uuid4()),
        discord=DiscordIdentity(discord_id),
        created_at=now,
        updated_at=now,
        role=role,
        access_granted_by=granted_by,
        access_granted_at=now if granted_by is not None else None,
    )


def _access_event(
    before: User,
    after: User,
    actor_id: UserId | None,
    occurred_at: datetime,
) -> AccessEvent:
    return AccessEvent(
        uuid4(),
        after.id,
        actor_id,
        AccessAction.OPERATOR_ROLE_CHANGED,
        before.role,
        after.role,
        occurred_at,
    )


def _validate_operator_id(discord_id: str) -> None:
    try:
        DiscordIdentity(discord_id)
    except ValueError as error:
        raise ValueError("Operator IDs must be Discord snowflakes.") from error


def _validate_discord_id(discord_id: str) -> None:
    try:
        DiscordIdentity(discord_id)
    except ValueError as error:
        raise AuthError(AuthErrorCode.INVALID_DISCORD_ID, 422) from error

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Private relational mappings and persistence for the User aggregate."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    LargeBinary,
    ForeignKey,
    Select,
    String,
    Uuid,
    delete,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, joinedload, mapped_column, relationship

from nahoermaar.database.schema import Base

from .domain import (
    AccessAction,
    AccessEvent,
    AccessRole,
    Appearance,
    BrowserSession,
    AppearanceMode,
    DiscordIdentity,
    FontFamily,
    IconSet,
    LoginAttempt,
    NeutralColor,
    PrimaryColor,
    TextSize,
    User,
    UserId,
    UserProfile,
)


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_ACCESS_ROLE = SqlEnum(
    AccessRole,
    name="access_role",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_ACCESS_ACTION = SqlEnum(
    AccessAction,
    name="access_action",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_ACCESS_ROLE_BEFORE = SqlEnum(
    AccessRole,
    name="access_role_before",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_ACCESS_ROLE_AFTER = SqlEnum(
    AccessRole,
    name="access_role_after",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_APPEARANCE_MODE = SqlEnum(
    AppearanceMode,
    name="appearance_mode",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_PRIMARY_COLOR = SqlEnum(
    PrimaryColor,
    name="primary_color",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_NEUTRAL_COLOR = SqlEnum(
    NeutralColor,
    name="neutral_color",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_FONT_FAMILY = SqlEnum(
    FontFamily,
    name="font_family",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_ICON_SET = SqlEnum(
    IconSet,
    name="icon_set",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_TEXT_SIZE = SqlEnum(
    TextSize,
    name="text_size",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'user' AND access_granted_by IS NOT NULL "
            "AND access_granted_at IS NOT NULL) OR "
            "(role IS DISTINCT FROM 'user' AND access_granted_by IS NULL "
            "AND access_granted_at IS NULL)",
            name="access_grant",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="update_not_before_creation",
        ),
        CheckConstraint(
            "last_login_at IS NULL OR last_login_at >= created_at",
            name="login_not_before_creation",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    role: Mapped[AccessRole | None] = mapped_column(_ACCESS_ROLE, index=True)
    access_granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    discord_identity: Mapped[_DiscordIdentityRow] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
        single_parent=True,
    )
    profile: Mapped[_UserProfileRow] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
        single_parent=True,
    )
    preferences: Mapped[_UserPreferencesRow] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
        single_parent=True,
    )


class _DiscordIdentityRow(Base):
    __tablename__ = "discord_identities"
    __table_args__ = (
        CheckConstraint(
            "discord_id ~ '^[1-9][0-9]{0,19}$' "
            "AND discord_id::numeric < 18446744073709551616",
            name="valid_snowflake",
        ),
        CheckConstraint(
            "(username IS NULL AND avatar_hash IS NULL AND synced_at IS NULL) OR "
            "(username IS NOT NULL AND synced_at IS NOT NULL)",
            name="metadata_complete",
        ),
        CheckConstraint(
            "username IS NULL OR "
            "(char_length(username) BETWEEN 1 AND 32 AND username = btrim(username))",
            name="username_valid",
        ),
        CheckConstraint(
            "avatar_hash IS NULL OR "
            "(char_length(avatar_hash) BETWEEN 1 AND 64 "
            "AND avatar_hash = btrim(avatar_hash))",
            name="avatar_hash_valid",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    discord_id: Mapped[str] = mapped_column(String(20), unique=True)
    username: Mapped[str | None] = mapped_column(String(32))
    avatar_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[_UserRow] = relationship(back_populates="discord_identity")


class _UserProfileRow(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "display_name IS NULL OR "
            "(char_length(display_name) BETWEEN 1 AND 32 "
            "AND display_name = btrim(display_name))",
            name="display_name_valid",
        ),
        CheckConstraint(
            "pixabot IS NULL OR pixabot ~ '^[0-9a-f]{4}$'",
            name="pixabot_valid",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(32))
    pixabot: Mapped[str | None] = mapped_column(String(4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[_UserRow] = relationship(back_populates="profile")


class _UserPreferencesRow(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[AppearanceMode] = mapped_column(_APPEARANCE_MODE)
    artwork_colors: Mapped[bool] = mapped_column(Boolean)
    primary_color: Mapped[PrimaryColor] = mapped_column(_PRIMARY_COLOR)
    neutral_color: Mapped[NeutralColor] = mapped_column(_NEUTRAL_COLOR)
    font_family: Mapped[FontFamily] = mapped_column(_FONT_FAMILY)
    icon_set: Mapped[IconSet] = mapped_column(_ICON_SET)
    text_size: Mapped[TextSize] = mapped_column(_TEXT_SIZE)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[_UserRow] = relationship(back_populates="preferences")


class _LoginAttemptRow(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
    )

    state_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    browser_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    verifier: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class _BrowserSessionRow(Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class _AccessEventRow(Base):
    __tablename__ = "access_events"
    __table_args__ = (
        CheckConstraint(
            "role_before IS DISTINCT FROM role_after",
            name="role_transition",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    subject_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[AccessAction] = mapped_column(_ACCESS_ACTION)
    role_before: Mapped[AccessRole | None] = mapped_column(_ACCESS_ROLE_BEFORE)
    role_after: Mapped[AccessRole | None] = mapped_column(_ACCESS_ROLE_AFTER)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UserNotFoundError(LookupError):
    """Raised when an aggregate update targets an unknown user."""


class UserRepository:
    """Persist complete User aggregates inside an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> User | None:
        """Load one user by its stable internal identity."""
        row = await self._get_row(user_id)
        return _to_domain(row) if row is not None else None

    async def get_by_discord_id(
        self, discord_id: str, *, for_update: bool = False
    ) -> User | None:
        """Load one user by its external Discord identity."""
        statement = (
            _aggregate_select()
            .join(_UserRow.discord_identity)
            .where(_DiscordIdentityRow.discord_id == discord_id)
        )
        if for_update:
            statement = statement.with_for_update(of=_UserRow)
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def has_access(self, user_id: UserId) -> bool:
        """Return whether the user currently has a NaHörMaar role."""
        role = await self._session.scalar(
            select(_UserRow.role).where(_UserRow.id == user_id)
        )
        return role is not None

    async def active_ids_by_discord_ids(
        self,
        discord_ids: Iterable[str],
    ) -> dict[str, UserId]:
        """Resolve only Discord identities with current NaHörMaar access."""
        identifiers = tuple(dict.fromkeys(discord_ids))
        if not identifiers:
            return {}
        rows = await self._session.execute(
            select(_DiscordIdentityRow.discord_id, _DiscordIdentityRow.user_id)
            .join(_UserRow, _UserRow.id == _DiscordIdentityRow.user_id)
            .where(
                _DiscordIdentityRow.discord_id.in_(identifiers),
                _UserRow.role.is_not(None),
            )
        )
        return {discord_id: UserId(user_id) for discord_id, user_id in rows.tuples()}

    async def privileged(self) -> tuple[User, ...]:
        """Load every persisted operator for startup reconciliation."""
        result = await self._session.scalars(
            _aggregate_select()
            .where(_UserRow.role.in_((AccessRole.OWNER, AccessRole.ADMIN)))
            .order_by(_UserRow.created_at, _UserRow.id)
        )
        return tuple(_to_domain(row) for row in result.unique())

    async def grants(self) -> tuple[User, ...]:
        """Load ordinary users in deterministic grant order."""
        result = await self._session.scalars(
            _aggregate_select()
            .where(_UserRow.role == AccessRole.USER)
            .order_by(_UserRow.access_granted_at, _UserRow.id)
        )
        return tuple(_to_domain(row) for row in result.unique())

    async def add_access_event(self, event: AccessEvent) -> None:
        """Stage one immutable audit fact after its referenced users."""
        await self._session.flush()
        self._session.add(
            _AccessEventRow(
                id=event.id,
                subject_user_id=event.subject_user_id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                role_before=event.role_before,
                role_after=event.role_after,
                occurred_at=event.occurred_at,
            )
        )

    async def access_history(self, limit: int = 100) -> tuple[AccessEvent, ...]:
        """Return newest access transitions first."""
        rows = await self._session.scalars(
            select(_AccessEventRow)
            .order_by(_AccessEventRow.occurred_at.desc(), _AccessEventRow.id.desc())
            .limit(limit)
        )
        return tuple(
            AccessEvent(
                row.id,
                UserId(row.subject_user_id),
                UserId(row.actor_user_id) if row.actor_user_id is not None else None,
                row.action,
                row.role_before,
                row.role_after,
                row.occurred_at,
            )
            for row in rows
        )

    def add(self, user: User) -> None:
        """Stage a new complete aggregate without committing the transaction."""
        self._session.add(
            _UserRow(
                id=user.id,
                role=user.role,
                access_granted_by=user.access_granted_by,
                access_granted_at=user.access_granted_at,
                created_at=user.created_at,
                updated_at=user.updated_at,
                last_login_at=user.last_login_at,
                discord_identity=_DiscordIdentityRow(
                    user_id=user.id,
                    discord_id=user.discord.discord_id,
                    username=user.discord.username,
                    avatar_hash=user.discord.avatar_hash,
                    synced_at=user.discord.synced_at,
                ),
                profile=_UserProfileRow(
                    user_id=user.id,
                    display_name=user.profile.display_name,
                    pixabot=user.profile.pixabot,
                    updated_at=user.updated_at,
                ),
                preferences=_UserPreferencesRow(
                    user_id=user.id,
                    mode=user.appearance.mode,
                    artwork_colors=user.appearance.artwork_colors,
                    primary_color=user.appearance.primary_color,
                    neutral_color=user.appearance.neutral_color,
                    font_family=user.appearance.font_family,
                    icon_set=user.appearance.icon_set,
                    text_size=user.appearance.text_size,
                    updated_at=user.updated_at,
                ),
            )
        )

    async def update(self, user: User) -> None:
        """Replace persisted aggregate values without committing."""
        row = await self._get_row(user.id)
        if row is None:
            raise UserNotFoundError(str(user.id))

        row.role = user.role
        row.access_granted_by = user.access_granted_by
        row.access_granted_at = user.access_granted_at
        row.updated_at = user.updated_at
        row.last_login_at = user.last_login_at

        row.discord_identity.discord_id = user.discord.discord_id
        row.discord_identity.username = user.discord.username
        row.discord_identity.avatar_hash = user.discord.avatar_hash
        row.discord_identity.synced_at = user.discord.synced_at

        row.profile.display_name = user.profile.display_name
        row.profile.pixabot = user.profile.pixabot
        row.profile.updated_at = user.updated_at

        row.preferences.mode = user.appearance.mode
        row.preferences.artwork_colors = user.appearance.artwork_colors
        row.preferences.primary_color = user.appearance.primary_color
        row.preferences.neutral_color = user.appearance.neutral_color
        row.preferences.font_family = user.appearance.font_family
        row.preferences.icon_set = user.appearance.icon_set
        row.preferences.text_size = user.appearance.text_size
        row.preferences.updated_at = user.updated_at

    async def _get_row(self, user_id: UserId) -> _UserRow | None:
        result = await self._session.execute(
            _aggregate_select().where(_UserRow.id == user_id)
        )
        return result.scalar_one_or_none()


def _aggregate_select() -> Select[tuple[_UserRow]]:
    return select(_UserRow).options(
        joinedload(_UserRow.discord_identity),
        joinedload(_UserRow.profile),
        joinedload(_UserRow.preferences),
    )


def _to_domain(row: _UserRow) -> User:
    identity = row.discord_identity
    profile = row.profile
    preferences = row.preferences
    return User(
        id=UserId(row.id),
        discord=DiscordIdentity(
            identity.discord_id,
            identity.username,
            identity.avatar_hash,
            identity.synced_at,
        ),
        profile=UserProfile(profile.display_name, profile.pixabot),
        appearance=Appearance(
            mode=preferences.mode,
            artwork_colors=preferences.artwork_colors,
            primary_color=preferences.primary_color,
            neutral_color=preferences.neutral_color,
            font_family=preferences.font_family,
            icon_set=preferences.icon_set,
            text_size=preferences.text_size,
        ),
        role=row.role,
        access_granted_by=(
            UserId(row.access_granted_by) if row.access_granted_by is not None else None
        ),
        access_granted_at=row.access_granted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_login_at=row.last_login_at,
    )


class AuthRepository:
    """Persist single-use OAuth attempts and revocable browser sessions."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin_login(
        self,
        attempt: LoginAttempt,
        *,
        maximum_active: int = 512,
    ) -> bool:
        """Replace this browser's attempt after clearing expired attempts."""
        await self._session.execute(
            delete(_LoginAttemptRow).where(
                _LoginAttemptRow.expires_at <= attempt.created_at
            )
        )
        await self._session.execute(
            delete(_LoginAttemptRow).where(
                _LoginAttemptRow.browser_hash == attempt.browser_hash
            )
        )
        active = await self._session.scalar(
            select(func.count()).select_from(_LoginAttemptRow)
        )
        if active is not None and active >= maximum_active:
            return False
        self._session.add(
            _LoginAttemptRow(
                state_hash=attempt.state_hash,
                browser_hash=attempt.browser_hash,
                verifier=attempt.verifier,
                created_at=attempt.created_at,
                expires_at=attempt.expires_at,
            )
        )
        return True

    async def consume_login(
        self,
        state_hash: bytes,
        browser_hash: bytes,
        now: datetime,
    ) -> str | None:
        """Atomically consume one unexpired browser-bound login attempt."""
        return await self._session.scalar(
            delete(_LoginAttemptRow)
            .where(
                _LoginAttemptRow.state_hash == state_hash,
                _LoginAttemptRow.browser_hash == browser_hash,
                _LoginAttemptRow.expires_at > now,
            )
            .returning(_LoginAttemptRow.verifier)
        )

    async def create_session(
        self,
        session: BrowserSession,
        *,
        previous_hash: bytes | None = None,
    ) -> None:
        """Rotate a browser session and clear expired rows."""
        await self._session.execute(
            delete(_BrowserSessionRow).where(
                _BrowserSessionRow.expires_at <= session.created_at
            )
        )
        if previous_hash is not None:
            await self._session.execute(
                delete(_BrowserSessionRow).where(
                    _BrowserSessionRow.token_hash == previous_hash
                )
            )
        self._session.add(
            _BrowserSessionRow(
                token_hash=session.token_hash,
                user_id=session.user_id,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        )

    async def session(self, token_hash: bytes, now: datetime) -> BrowserSession | None:
        """Load one unexpired session without extending it."""
        row = await self._session.scalar(
            select(_BrowserSessionRow).where(
                _BrowserSessionRow.token_hash == token_hash,
                _BrowserSessionRow.expires_at > now,
            )
        )
        if row is None:
            return None
        return BrowserSession(
            row.token_hash,
            UserId(row.user_id),
            row.created_at,
            row.expires_at,
        )

    async def delete_session(self, token_hash: bytes) -> None:
        """Revoke one browser token if it exists."""
        await self._session.execute(
            delete(_BrowserSessionRow).where(
                _BrowserSessionRow.token_hash == token_hash
            )
        )

    async def delete_user_sessions(self, user_id: UserId) -> None:
        """Revoke every browser token owned by one user."""
        await self._session.execute(
            delete(_BrowserSessionRow).where(_BrowserSessionRow.user_id == user_id)
        )

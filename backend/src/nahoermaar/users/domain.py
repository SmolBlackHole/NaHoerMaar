# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""User identity, profile, access and appearance values."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)

_DISCORD_ID = re.compile(r"[1-9][0-9]{0,19}")
_PIXABOT = re.compile(r"[0-9a-f]{4}")


class AccessRole(StrEnum):
    """Roles understood by the access policy."""

    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"

    @property
    def privileged(self) -> bool:
        """Return whether only the host configuration may assign this role."""
        return self in {AccessRole.OWNER, AccessRole.ADMIN}


class AccessAction(StrEnum):
    """Auditable changes to a user's access."""

    GRANTED = "granted"
    REVOKED = "revoked"
    OPERATOR_ROLE_CHANGED = "operator_role_changed"


class AuthErrorCode(StrEnum):
    """Stable failures exposed by the authentication and access boundary."""

    SIGNED_OUT = "signed_out"
    LOGIN_UNAVAILABLE = "login_unavailable"
    LOGIN_BUSY = "login_busy"
    LOGIN_EXPIRED = "login_expired"
    LOGIN_CANCELLED = "login_cancelled"
    LOGIN_FAILED = "login_failed"
    ACCESS_DENIED = "access_denied"
    ACCESS_UNAVAILABLE = "access_unavailable"
    INVALID_DISCORD_ID = "invalid_discord_id"
    OPERATOR_ACCESS_MANAGED_IN_CONFIG = "operator_access_managed_in_config"
    GRANT_NOT_OWNED = "grant_not_owned"
    PROFILE_NOT_FOUND = "profile_not_found"
    ORIGIN_FORBIDDEN = "origin_forbidden"
    CSRF_FAILED = "csrf_failed"


class AuthError(RuntimeError):
    """Expected authentication or authorization failure."""

    def __init__(self, code: AuthErrorCode, status: int = 400) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status


class AppearanceMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"
    TIME = "time"


class PrimaryColor(StrEnum):
    RED = "red"
    ORANGE = "orange"
    AMBER = "amber"
    YELLOW = "yellow"
    LIME = "lime"
    GREEN = "green"
    EMERALD = "emerald"
    TEAL = "teal"
    CYAN = "cyan"
    SKY = "sky"
    BLUE = "blue"
    INDIGO = "indigo"
    VIOLET = "violet"
    PURPLE = "purple"
    FUCHSIA = "fuchsia"
    PINK = "pink"
    ROSE = "rose"


class NeutralColor(StrEnum):
    SLATE = "slate"
    GRAY = "gray"
    ZINC = "zinc"
    NEUTRAL = "neutral"
    STONE = "stone"


class FontFamily(StrEnum):
    PUBLIC_SANS = "Public Sans"
    DM_SANS = "DM Sans"
    GEIST = "Geist"
    INTER = "Inter"
    POPPINS = "Poppins"
    OUTFIT = "Outfit"
    RALEWAY = "Raleway"


class IconSet(StrEnum):
    LUCIDE = "lucide"
    PHOSPHOR = "ph"
    HEROICONS = "heroicons"
    TABLER = "tabler"


class TextSize(StrEnum):
    SMALL = "sm"
    MEDIUM = "md"
    LARGE = "lg"


@dataclass(frozen=True, slots=True)
class Appearance:
    """Relational appearance preferences owned by one user."""

    mode: AppearanceMode = AppearanceMode.DARK
    artwork_colors: bool = True
    primary_color: PrimaryColor = PrimaryColor.TEAL
    neutral_color: NeutralColor = NeutralColor.ZINC
    font_family: FontFamily = FontFamily.GEIST
    icon_set: IconSet = IconSet.LUCIDE
    text_size: TextSize = TextSize.MEDIUM


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    """The external Discord identity linked to an internal user."""

    discord_id: str
    username: str | None = None
    avatar_hash: str | None = None
    synced_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            _DISCORD_ID.fullmatch(self.discord_id) is None
            or int(self.discord_id) >= 2**64
        ):
            raise ValueError("Discord ID must be an unsigned decimal snowflake.")
        if self.username is None:
            if self.avatar_hash is not None or self.synced_at is not None:
                raise ValueError(
                    "An unsynchronized Discord identity cannot contain metadata."
                )
            return
        if not 1 <= len(self.username) <= 32 or self.username != self.username.strip():
            raise ValueError("Discord username must be trimmed and 1 to 32 characters.")
        if self.avatar_hash is not None and (
            not self.avatar_hash or self.avatar_hash != self.avatar_hash.strip()
        ):
            raise ValueError("Discord avatar hash must be non-empty and trimmed.")
        _require_aware(self.synced_at, "Discord sync time")

    @property
    def complete(self) -> bool:
        """Return whether Discord supplied the identity metadata."""
        return self.username is not None


@dataclass(frozen=True, slots=True)
class DiscordMember:
    """One human Discord member visible to the configured bot."""

    discord_id: str
    username: str
    display_name: str
    avatar_url: str | None
    guild_id: str
    guild_name: str


@dataclass(frozen=True, slots=True)
class UserProfile:
    """NaHörMaar-owned values that never overwrite the Discord identity."""

    display_name: str | None = None
    pixabot: str | None = None

    def __post_init__(self) -> None:
        if self.display_name is not None and (
            not 1 <= len(self.display_name) <= 32
            or self.display_name != self.display_name.strip()
        ):
            raise ValueError("Display name must be trimmed and 1 to 32 characters.")
        if self.pixabot is not None and _PIXABOT.fullmatch(self.pixabot) is None:
            raise ValueError("Pixabot must be a four-character lowercase hex ID.")

    @property
    def complete(self) -> bool:
        """Return whether the local profile can be shown as configured."""
        return self.display_name is not None and self.pixabot is not None


@dataclass(frozen=True, slots=True)
class User:
    """Stable internal identity and its current user-owned state."""

    id: UserId
    discord: DiscordIdentity
    created_at: datetime
    updated_at: datetime
    profile: UserProfile = field(default_factory=UserProfile)
    appearance: Appearance = field(default_factory=Appearance)
    role: AccessRole | None = None
    access_granted_by: UserId | None = None
    access_granted_at: datetime | None = None
    last_login_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "User creation time")
        _require_aware(self.updated_at, "User update time")
        if self.updated_at < self.created_at:
            raise ValueError("User update time cannot precede creation.")
        if self.last_login_at is not None:
            _require_aware(self.last_login_at, "Last login time")
            if self.last_login_at < self.created_at:
                raise ValueError("Last login time cannot precede creation.")

        has_grant = (
            self.access_granted_by is not None and self.access_granted_at is not None
        )
        has_partial_grant = (
            self.access_granted_by is not None or self.access_granted_at is not None
        )
        if has_partial_grant and not has_grant:
            raise ValueError("Access grant actor and time must be stored together.")
        if self.role is AccessRole.USER:
            if not has_grant:
                raise ValueError("A user role requires its granting user and time.")
            _require_aware(self.access_granted_at, "Access grant time")
        elif has_grant:
            raise ValueError("Only the user role carries API grant metadata.")

    @property
    def has_access(self) -> bool:
        """Return whether the user may authenticate into NaHörMaar."""
        return self.role is not None

    @property
    def profile_complete(self) -> bool:
        """Return the derived local profile state."""
        return self.profile.complete


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """A revocable browser login referencing one internal user."""

    token_hash: bytes = field(repr=False)
    user_id: UserId
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.token_hash) != 32:
            raise ValueError("Session token hash must contain 32 bytes.")
        _require_aware(self.created_at, "Session creation time")
        _require_aware(self.expires_at, "Session expiry time")
        if self.expires_at <= self.created_at:
            raise ValueError("Session expiry must follow its creation.")


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    """Single-use PKCE state bound to one temporary browser cookie."""

    state_hash: bytes = field(repr=False)
    browser_hash: bytes = field(repr=False)
    verifier: str = field(repr=False)
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.state_hash) != 32 or len(self.browser_hash) != 32:
            raise ValueError("Login state and browser hashes must contain 32 bytes.")
        if not 43 <= len(self.verifier) <= 128:
            raise ValueError("PKCE verifier must contain 43 to 128 characters.")
        _require_aware(self.created_at, "Login creation time")
        _require_aware(self.expires_at, "Login expiry time")
        if self.expires_at <= self.created_at:
            raise ValueError("Login expiry must follow its creation.")


@dataclass(frozen=True, slots=True)
class AccessEvent:
    """Immutable audit fact for one access-role transition."""

    id: UUID
    subject_user_id: UserId
    actor_user_id: UserId | None
    action: AccessAction
    role_before: AccessRole | None
    role_after: AccessRole | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.occurred_at, "Access event time")
        if self.role_before is self.role_after:
            raise ValueError("An access event must describe a role transition.")


@dataclass(frozen=True, slots=True)
class Authenticated:
    """A valid browser session and its current user state."""

    user: User
    expires_at: datetime
    csrf: str = field(repr=False)

    @property
    def admin(self) -> bool:
        """Return whether the user may administer ordinary access grants."""
        return self.user.role is not None and self.user.role.privileged


def _require_aware(value: datetime | None, label: str) -> None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")

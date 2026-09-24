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


def _require_aware(value: datetime | None, label: str) -> None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")

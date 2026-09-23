# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Access roles and immutable grant history."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccessRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"

    @property
    def privileged(self) -> bool:
        return self in {AccessRole.OWNER, AccessRole.ADMIN}


@dataclass(frozen=True, slots=True)
class AccessGrant:
    discord_id: str
    granted_by: str
    granted_at: datetime
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AccessEvent:
    id: UUID
    action: str
    discord_id: str
    actor_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DiscordMember:
    discord_id: str
    name: str
    display_name: str
    avatar_url: str | None
    guild_id: str
    guild_name: str

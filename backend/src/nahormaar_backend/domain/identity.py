# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Validated Discord identities and authentication failures."""

import re
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Contributor:
    """Account identity and display values captured for a listener's action."""

    id: UUID
    name: str
    avatar: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.name) <= 32 or self.name != self.name.strip():
            raise ValueError(
                "A contributor needs a trimmed name of 1 to 32 characters."
            )
        if not re.fullmatch(r"[0-9a-f]{4}", self.avatar):
            raise ValueError("Invalid contributor avatar.")


def discord_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
        and int(value) < 2**64
    )


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    id: str
    name: str

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Validated Discord identities and authentication failures."""

import re
from dataclasses import dataclass


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

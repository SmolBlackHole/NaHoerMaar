# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Live Discord whitelist shared by dashboard sessions and bot commands."""

import tomllib
from pathlib import Path
from typing import cast

from ..domain.identity import AuthError, discord_id


def require_access(path: Path, identifier: str) -> bool:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        identifiers = data.get("discord_ids")
        admins = data.get("admin_ids", [])
        if (
            set(data) not in ({"discord_ids"}, {"discord_ids", "admin_ids"})
            or not isinstance(identifiers, list)
            or not all(discord_id(item) for item in cast(list[object], identifiers))
            or not isinstance(admins, list)
            or not all(discord_id(item) for item in cast(list[object], admins))
            or not set(cast(list[str], admins)).issubset(cast(list[str], identifiers))
        ):
            raise ValueError("Invalid access list.")
    except (OSError, ValueError) as exc:
        raise AuthError("access_unavailable", 503) from exc
    if identifier not in identifiers:
        raise AuthError("access_denied", 403)
    return identifier in admins

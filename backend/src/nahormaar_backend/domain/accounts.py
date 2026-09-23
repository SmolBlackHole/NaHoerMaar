# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Account values independent of database records."""

from dataclasses import dataclass
from datetime import datetime

from .access import AccessRole
from .identity import Contributor
from .preferences import Appearance


@dataclass(frozen=True, slots=True)
class Account:
    profile: Contributor
    discord_id: str
    role: AccessRole | None
    access_granted_by: str | None
    access_granted_at: datetime | None
    profile_complete: bool
    appearance: Appearance

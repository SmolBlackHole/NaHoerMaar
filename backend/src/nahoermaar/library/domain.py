# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Domain values and failures owned by the personal Library."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nahoermaar.catalog.domain import TrackId
from nahoermaar.users.domain import UserId


class ReactionValue(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


class LibraryErrorCode(StrEnum):
    TRACK_NOT_FOUND = "library_track_not_found"


class LibraryError(RuntimeError):
    """Expected Library failure with a stable public code."""

    def __init__(self, code: LibraryErrorCode, status: int) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class TrackReaction:
    user_id: UserId
    track_id: TrackId
    value: ReactionValue
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _aware(self.created_at, "Reaction creation time")
        _aware(self.updated_at, "Reaction update time")
        if self.updated_at < self.created_at:
            raise ValueError("Reaction update cannot precede its creation.")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")

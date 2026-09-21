# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The last audible position and connection to restore after a process restart."""

from dataclasses import dataclass
from math import isfinite
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PlaybackCheckpoint:
    channel_id: int
    entry_id: UUID | None
    position_seconds: float = 0
    paused: bool = False
    volume: float = 1
    history_recorded: bool = False

    def __post_init__(self) -> None:
        if self.channel_id <= 0:
            raise ValueError("A playback checkpoint needs a voice channel.")
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Invalid checkpoint position.")
        if not isfinite(self.volume) or not 0 <= self.volume <= 1:
            raise ValueError("Invalid checkpoint volume.")
        if self.entry_id is None and (self.position_seconds or self.paused):
            raise ValueError("An idle checkpoint has no playback position.")
        if self.entry_id is None and self.history_recorded:
            raise ValueError("An idle checkpoint has no confirmed play.")

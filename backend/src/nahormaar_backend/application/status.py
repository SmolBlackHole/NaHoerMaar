# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Published playback status and command replies."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from ..domain.commands import Outcome
from ..domain.models import PlayerSnapshot, QueueEntry
from ..domain.radio import RadioStatus


@dataclass(frozen=True, slots=True)
class PlaybackIssue:
    entry_id: UUID | None
    message: str
    fatal: bool = False
    id: UUID = field(default_factory=uuid4)
    entry: QueueEntry | None = None
    reason: Literal["source_unavailable", "stream_interrupted", "voice_unavailable"] = (
        "voice_unavailable"
    )


@dataclass(frozen=True, slots=True)
class PlaybackStatus:
    player: PlayerSnapshot
    attempt_id: UUID | None
    channel_id: int | None
    volume: float
    last_issue: PlaybackIssue | None
    revision: int = 0
    queue_revision: int = 0
    position_seconds: float = 0
    position_updated_at: datetime | None = None
    radio: RadioStatus = field(default_factory=RadioStatus)


@dataclass(frozen=True, slots=True)
class CommandReply:
    outcome: Outcome
    status: PlaybackStatus
    replayed: bool = False

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Public JSON contracts; stream URLs and voice internals stay private."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    ANONYMOUS_CONTRIBUTOR,
    Contributor,
    PlaybackState,
    QueueEntry,
    VoiceState,
)
from .playback import PlaybackStatus
from .youtube import video_id


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ContributorData(Input):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: UUID
    name: str = Field(min_length=1, max_length=32)
    avatar: str = Field(pattern=r"^[0-9a-f]{4}$")

    def to_contributor(self) -> Contributor:
        return Contributor(self.id, self.name, self.avatar)

    @staticmethod
    def default_contributor() -> Contributor:
        return ANONYMOUS_CONTRIBUTOR


class AddInput(Input):
    source_url: str = Field(max_length=2048)
    added_by: ContributorData | None = None

    @field_validator("source_url")
    @classmethod
    def youtube_video(cls, value: str) -> str:
        identifier = video_id(value.strip())
        if identifier is None:
            raise ValueError("Use a single public YouTube video URL.")
        return f"https://www.youtube.com/watch?v={identifier}"


class QueueRevisionInput(Input):
    expected_queue_revision: int = Field(ge=0, strict=True)


class MoveInput(QueueRevisionInput):
    before_entry_id: UUID | None


class ControlInput(Input):
    expected_playback_id: UUID | None


class VolumeInput(Input):
    volume: float = Field(ge=0, le=1, strict=True)


class SeekInput(Input):
    position_seconds: float = Field(ge=0, strict=True)
    expected_playback_id: UUID


class ChannelInput(Input):
    channel_id: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]

    @field_validator("channel_id")
    @classmethod
    def snowflake(cls, value: str) -> str:
        if int(value) >= 2**64:
            raise ValueError("Invalid Discord channel ID.")
        return value


class Entry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_url: str
    video_id: str | None
    title: str | None
    uploader: str | None
    duration_seconds: float | None
    thumbnail_url: str | None
    artist: str | None
    uploader_url: str | None
    added_by: ContributorData | None

    @classmethod
    def from_entry(cls, entry: QueueEntry) -> "Entry":
        return cls.model_validate(entry)


class Issue(BaseModel):
    entry_id: UUID | None
    code: Literal["playback_failed", "backend_halted"]
    fatal: bool


class RecentEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    played_at: datetime
    entry: Entry


class State(BaseModel):
    revision: int
    queue_revision: int
    state: PlaybackState
    current: Entry | None
    upcoming: tuple[Entry, ...]
    recently_played: tuple[RecentEntry, ...]
    voice_state: VoiceState
    channel_id: str | None
    playback_id: UUID | None
    volume: float
    position_seconds: float
    position_updated_at: datetime | None
    last_issue: Issue | None

    @classmethod
    def from_status(cls, status: PlaybackStatus) -> "State":
        player = status.player
        issue = status.last_issue
        return cls(
            revision=status.revision,
            queue_revision=status.queue_revision,
            state=player.state,
            current=Entry.from_entry(player.current) if player.current else None,
            upcoming=tuple(Entry.from_entry(entry) for entry in player.upcoming),
            recently_played=tuple(
                RecentEntry.model_validate(item) for item in player.recently_played
            ),
            voice_state=player.voice_state,
            channel_id=str(status.channel_id)
            if status.channel_id is not None
            else None,
            playback_id=status.attempt_id,
            volume=status.volume,
            position_seconds=status.position_seconds,
            position_updated_at=status.position_updated_at,
            last_issue=Issue(
                entry_id=issue.entry_id,
                fatal=issue.fatal,
                code="backend_halted" if issue.fatal else "playback_failed",
            )
            if issue
            else None,
        )


class Channel(BaseModel):
    id: str
    name: str
    can_connect: bool
    can_speak: bool


class MutationResult(BaseModel):
    request_id: UUID
    code: str
    entry_id: UUID | None
    replayed: bool
    snapshot: State

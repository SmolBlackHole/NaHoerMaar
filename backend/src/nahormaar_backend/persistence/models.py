# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""SQLAlchemy mappings for the complete database schema."""

from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class QueueEntryRow(Base):
    __tablename__ = "queue_entries"
    __table_args__ = (CheckConstraint("position >= 0"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(unique=True)
    source_url: Mapped[str]
    video_id: Mapped[str | None]
    title: Mapped[str | None]
    uploader: Mapped[str | None]
    duration_seconds: Mapped[float | None]
    thumbnail_url: Mapped[str | None]
    artist: Mapped[str | None]
    uploader_url: Mapped[str | None]
    added_by: Mapped[dict[str, str] | None] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(default="manual", server_default="manual")


class HistoryRow(Base):
    __tablename__ = "playback_history"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(unique=True)
    played_at: Mapped[str]
    entry_id: Mapped[UUID]
    source_url: Mapped[str]
    video_id: Mapped[str | None]
    title: Mapped[str | None]
    uploader: Mapped[str | None]
    duration_seconds: Mapped[float | None]
    thumbnail_url: Mapped[str | None]
    artist: Mapped[str | None]
    uploader_url: Mapped[str | None]
    added_by: Mapped[dict[str, str] | None] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(default="manual", server_default="manual")


class PlayerRow(Base):
    __tablename__ = "player_state"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str]
    current_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("queue_entries.id")
    )
    revision: Mapped[int] = mapped_column(default=0, server_default="0")
    queue_revision: Mapped[int] = mapped_column(default=0, server_default="0")
    crossfade_seconds: Mapped[int] = mapped_column(default=0, server_default="0")


class PlaybackCheckpointRow(Base):
    __tablename__ = "playback_checkpoint"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int]
    entry_id: Mapped[UUID | None]
    position_seconds: Mapped[float]
    paused: Mapped[bool]
    volume: Mapped[float]


class RequestRow(Base):
    __tablename__ = "requests"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint: Mapped[str]
    code: Mapped[str | None]
    status_code: Mapped[int | None]
    entry_id: Mapped[UUID | None]
    actor_id: Mapped[UUID | None]
    details: Mapped[dict[str, object] | None] = mapped_column(JSON)


class UndoRow(Base):
    __tablename__ = "queue_undo"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID]
    expires_at: Mapped[float] = mapped_column(index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    discord_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    avatar: Mapped[str]
    profile_complete: Mapped[bool] = mapped_column(default=False)
    appearance: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, server_default="{}"
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    expires_at: Mapped[float]


class LoginRow(Base):
    __tablename__ = "login_attempts"

    state_hash: Mapped[str] = mapped_column(primary_key=True)
    browser_hash: Mapped[str] = mapped_column(unique=True)
    verifier: Mapped[str]
    expires_at: Mapped[float]

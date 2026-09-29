# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Transactional persistence owned by the personal Library."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.schema import Base, enum_values
from nahoermaar.users.domain import UserId

from .domain import ReactionValue, TrackReaction


_REACTION = SqlEnum(
    ReactionValue,
    name="track_reaction_value",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=enum_values,
)


class _TrackReactionRow(Base):
    __tablename__ = "track_reactions"
    __table_args__ = (
        CheckConstraint("updated_at >= created_at", name="update_not_before_creation"),
        Index(
            "ix_track_reactions_user_value_updated_track",
            "user_id",
            "value",
            "updated_at",
            "track_id",
        ),
        Index("ix_track_reactions_track_value", "track_id", "value"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), primary_key=True
    )
    value: Mapped[ReactionValue] = mapped_column(_REACTION)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrackReactionRepository:
    """Write reactions inside an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId, track_id: TrackId) -> TrackReaction | None:
        row = await self._session.scalar(
            select(_TrackReactionRow).where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
        )
        return _reaction(row) if row is not None else None

    async def set(
        self,
        user_id: UserId,
        track_id: TrackId,
        value: ReactionValue,
        now: datetime,
    ) -> TrackReaction:
        row = await self._session.scalar(
            select(_TrackReactionRow)
            .where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
            .with_for_update()
        )
        if row is None:
            row = _TrackReactionRow(
                user_id=user_id,
                track_id=track_id,
                value=value,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
            await self._session.flush()
        elif row.value is not value:
            row.value = value
            row.updated_at = now
            await self._session.flush()
        return _reaction(row)

    async def remove(self, user_id: UserId, track_id: TrackId) -> bool:
        row = await self._session.scalar(
            select(_TrackReactionRow)
            .where(
                _TrackReactionRow.user_id == user_id,
                _TrackReactionRow.track_id == track_id,
            )
            .with_for_update()
        )
        if row is None:
            return False
        await self._session.delete(row)
        return True


def _reaction(row: _TrackReactionRow) -> TrackReaction:
    return TrackReaction(
        UserId(row.user_id),
        TrackId(row.track_id),
        row.value,
        row.created_at,
        row.updated_at,
    )

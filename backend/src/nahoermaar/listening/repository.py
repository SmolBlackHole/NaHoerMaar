# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persistence for durable track requests."""

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Uuid,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.database.schema import Base
from nahoermaar.player.domain import (
    ListeningSessionId,
    RadioRunId,
    RequestOrigin,
    TrackRequest,
    TrackRequestId,
)
from nahoermaar.users.domain import UserId
from nahoermaar.catalog.domain import TrackId, TrackSourceId


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_REQUEST_ORIGIN = SqlEnum(
    RequestOrigin,
    name="request_origin",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _TrackRequestRow(Base):
    __tablename__ = "track_requests"
    __table_args__ = (
        CheckConstraint(
            "(origin = 'manual' AND requested_by IS NOT NULL AND radio_run_id IS NULL) "
            "OR (origin = 'radio' AND requested_by IS NULL AND radio_run_id IS NOT NULL)",
            name="origin_owner",
        ),
        Index("ix_track_requests_session_requested", "session_id", "requested_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT")
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), index=True
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("track_sources.id", ondelete="RESTRICT"), index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[RequestOrigin] = mapped_column(_REQUEST_ORIGIN)
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    radio_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("radio_runs.id", ondelete="RESTRICT"), index=True
    )


class ListeningRepository:
    """Append and rehydrate durable request facts in an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_requests(self, requests: Iterable[TrackRequest]) -> None:
        values = [
            {
                "id": request.id,
                "session_id": request.session_id,
                "track_id": request.track_id,
                "source_id": request.source_id,
                "requested_at": request.requested_at,
                "origin": request.origin,
                "requested_by": request.requested_by,
                "radio_run_id": request.radio_run_id,
            }
            for request in requests
        ]
        if values:
            await self._session.execute(
                insert(_TrackRequestRow)
                .values(values)
                .on_conflict_do_nothing(index_elements=[_TrackRequestRow.id])
            )

    async def requests(
        self,
        request_ids: Iterable[TrackRequestId],
    ) -> dict[TrackRequestId, TrackRequest]:
        identifiers = tuple(dict.fromkeys(request_ids))
        if not identifiers:
            return {}
        rows = (
            await self._session.scalars(
                select(_TrackRequestRow).where(_TrackRequestRow.id.in_(identifiers))
            )
        ).all()
        return {
            TrackRequestId(row.id): TrackRequest(
                TrackRequestId(row.id),
                ListeningSessionId(row.session_id),
                TrackId(row.track_id),
                TrackSourceId(row.source_id) if row.source_id is not None else None,
                row.requested_at,
                row.origin,
                UserId(row.requested_by) if row.requested_by is not None else None,
                RadioRunId(row.radio_run_id) if row.radio_run_id is not None else None,
            )
            for row in rows
        }

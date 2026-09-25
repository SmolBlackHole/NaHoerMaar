# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Relational persistence for requests, plays, presence and heard time."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Uuid,
    select,
    text,
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

from .domain import (
    AudienceMember,
    AudienceState,
    ListenerPresence,
    ListenerPresenceId,
    ListeningError,
    ListeningErrorCode,
    PlaybackEndReason,
    PlaybackListener,
    PlaybackProgress,
    PlaybackRecord,
    PlaybackRecordId,
)


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
_PLAYBACK_END_REASON = SqlEnum(
    PlaybackEndReason,
    name="playback_end_reason",
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
        Index("ix_track_requests_requested_at", "requested_at"),
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


class _PlaybackRecordRow(Base):
    __tablename__ = "playback_records"
    __table_args__ = (
        CheckConstraint("audio_seconds >= 0", name="audio_seconds_non_negative"),
        CheckConstraint(
            "group_audio_seconds >= 0 AND group_audio_seconds <= audio_seconds",
            name="group_audio_seconds_valid",
        ),
        CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND end_reason IS NOT NULL "
            "AND ended_at >= started_at)",
            name="end_valid",
        ),
        Index("ix_playback_records_session_started", "session_id", "started_at"),
        Index("ix_playback_records_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_requests.id", ondelete="RESTRICT"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    audio_seconds: Mapped[float] = mapped_column(Float)
    group_audio_seconds: Mapped[float] = mapped_column(Float)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[PlaybackEndReason | None] = mapped_column(_PLAYBACK_END_REASON)


class _ListenerPresenceRow(Base):
    __tablename__ = "listener_presence"
    __table_args__ = (
        CheckConstraint(
            "confirmed_at >= joined_at", name="confirmation_not_before_join"
        ),
        CheckConstraint(
            "left_at IS NULL OR left_at >= confirmed_at",
            name="leave_not_before_confirmation",
        ),
        Index(
            "uq_listener_presence_active",
            "session_id",
            "user_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deafened: Mapped[bool] = mapped_column(Boolean)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _PlaybackListenerRow(Base):
    __tablename__ = "playback_listeners"
    __table_args__ = (
        CheckConstraint("audio_seconds > 0", name="audio_seconds_positive"),
        CheckConstraint("last_heard_at >= first_heard_at", name="heard_interval_valid"),
        Index("ix_playback_listeners_user_id", "user_id"),
    )

    playback_id: Mapped[UUID] = mapped_column(
        ForeignKey("playback_records.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    audio_seconds: Mapped[float] = mapped_column(Float)
    first_heard_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heard_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ListeningRepository:
    """Write listening facts inside an existing unit of work."""

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

    async def start_playback(
        self,
        record: PlaybackRecord,
    ) -> tuple[PlaybackRecord, bool]:
        request = (await self.requests((record.request_id,))).get(record.request_id)
        if request is None or request.session_id != record.session_id:
            raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)

        row = await self._session.get(_PlaybackRecordRow, record.id)
        if row is not None:
            current = _playback(row)
            if current != record:
                raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)
            return current, False

        self._session.add(
            _PlaybackRecordRow(
                id=record.id,
                session_id=record.session_id,
                request_id=record.request_id,
                started_at=record.started_at,
                audio_seconds=record.audio_seconds,
                group_audio_seconds=record.group_audio_seconds,
                ended_at=record.ended_at,
                end_reason=record.end_reason,
            )
        )
        await self._session.flush()
        return record, True

    async def advance_playback(
        self,
        session_id: ListeningSessionId,
        progress: tuple[PlaybackProgress, ...],
        *,
        credited_playback_id: PlaybackRecordId,
        audience: AudienceState,
        observed_at: datetime,
    ) -> tuple[tuple[PlaybackRecord, ...], bool]:
        if audience.session_id != session_id:
            raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
        identifiers = tuple(item.playback_id for item in progress)
        if len(set(identifiers)) != len(identifiers):
            raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)
        rows = (
            await self._session.scalars(
                select(_PlaybackRecordRow)
                .where(
                    _PlaybackRecordRow.id.in_(identifiers),
                    _PlaybackRecordRow.session_id == session_id,
                )
                .with_for_update()
            )
        ).all()
        by_id = {PlaybackRecordId(row.id): row for row in rows}
        if len(by_id) != len(progress):
            raise ListeningError(ListeningErrorCode.PLAYBACK_NOT_FOUND)

        deltas: dict[PlaybackRecordId, float] = {}
        for item in progress:
            row = by_id[item.playback_id]
            if row.ended_at is not None:
                raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)
            if item.audio_seconds < row.audio_seconds:
                raise ListeningError(ListeningErrorCode.PROGRESS_REGRESSION)
            deltas[item.playback_id] = item.audio_seconds - row.audio_seconds
            row.audio_seconds = item.audio_seconds

        try:
            listener_delta = deltas[credited_playback_id]
        except KeyError as error:
            raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT) from error
        changed = any(delta > 0 for delta in deltas.values())
        if changed:
            await self._confirm_audience(session_id, audience, observed_at)
        if listener_delta > 0:
            if audience.observed_at is not None and audience.observed_at > observed_at:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            if audience.audible_human_count > 0 and audience.observed_at is not None:
                interval_start = observed_at - timedelta(seconds=listener_delta)
                credited_from = max(interval_start, audience.observed_at)
                by_id[credited_playback_id].group_audio_seconds += min(
                    listener_delta,
                    max(0.0, (observed_at - credited_from).total_seconds()),
                )
            await self._credit_listeners(
                session_id,
                credited_playback_id,
                audience,
                observed_at,
                listener_delta,
            )

        await self._session.flush()
        return (
            tuple(_playback(by_id[item.playback_id]) for item in progress),
            changed,
        )

    async def finish_playback(
        self,
        playback_id: PlaybackRecordId,
        *,
        ended_at: datetime,
        reason: PlaybackEndReason,
    ) -> tuple[PlaybackRecord, bool]:
        row = await self._session.scalar(
            select(_PlaybackRecordRow)
            .where(_PlaybackRecordRow.id == playback_id)
            .with_for_update()
        )
        if row is None:
            raise ListeningError(ListeningErrorCode.PLAYBACK_NOT_FOUND)
        if row.ended_at is not None:
            if row.ended_at != ended_at or row.end_reason != reason:
                raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)
            return _playback(row), False
        if ended_at < row.started_at:
            raise ListeningError(ListeningErrorCode.PLAYBACK_CONFLICT)
        row.ended_at = ended_at
        row.end_reason = reason
        await self._session.flush()
        return _playback(row), True

    async def playback(
        self,
        playback_id: PlaybackRecordId,
    ) -> PlaybackRecord | None:
        row = await self._session.get(_PlaybackRecordRow, playback_id)
        return _playback(row) if row is not None else None

    async def playback_listeners(
        self,
        playback_id: PlaybackRecordId,
    ) -> tuple[PlaybackListener, ...]:
        rows = (
            await self._session.scalars(
                select(_PlaybackListenerRow)
                .where(_PlaybackListenerRow.playback_id == playback_id)
                .order_by(_PlaybackListenerRow.user_id)
            )
        ).all()
        return tuple(_listener(row) for row in rows)

    async def presence_history(
        self,
        session_id: ListeningSessionId,
    ) -> tuple[ListenerPresence, ...]:
        rows = (
            await self._session.scalars(
                select(_ListenerPresenceRow)
                .where(_ListenerPresenceRow.session_id == session_id)
                .order_by(
                    _ListenerPresenceRow.joined_at,
                    _ListenerPresenceRow.id,
                )
            )
        ).all()
        return tuple(_presence(row) for row in rows)

    async def reconcile_audience(
        self,
        audience: AudienceState,
    ) -> tuple[ListenerPresence, ...]:
        if audience.observed_at is None:
            raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
        active = list(
            (
                await self._session.scalars(
                    select(_ListenerPresenceRow)
                    .where(
                        _ListenerPresenceRow.session_id == audience.session_id,
                        _ListenerPresenceRow.left_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        current = {UserId(row.user_id): row for row in active}
        observed_at = audience.observed_at
        members = {member.user_id: member for member in audience.members}

        for user_id, row in current.items():
            if observed_at < row.confirmed_at:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            member = members.get(user_id)
            if member is None:
                row.left_at = observed_at
            else:
                row.confirmed_at = observed_at
                row.deafened = member.deafened

        for user_id, member in members.items():
            if user_id in current:
                continue
            row = _ListenerPresenceRow(
                id=uuid4(),
                session_id=audience.session_id,
                user_id=user_id,
                joined_at=observed_at,
                confirmed_at=observed_at,
                deafened=member.deafened,
                left_at=None,
            )
            self._session.add(row)
            active.append(row)

        await self._session.flush()
        return tuple(_presence(row) for row in active if row.left_at is None)

    async def suspend_audience(
        self,
        session_id: ListeningSessionId,
        *,
        disconnected_at: datetime | None = None,
    ) -> AudienceState:
        rows = list(
            (
                await self._session.scalars(
                    select(_ListenerPresenceRow)
                    .where(
                        _ListenerPresenceRow.session_id == session_id,
                        _ListenerPresenceRow.left_at.is_(None),
                    )
                    .order_by(_ListenerPresenceRow.user_id)
                    .with_for_update()
                )
            ).all()
        )
        for row in rows:
            if disconnected_at is not None and disconnected_at < row.confirmed_at:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            row.left_at = disconnected_at or row.confirmed_at
        await self._session.flush()
        return AudienceState(
            session_id,
            len(rows),
            sum(not row.deafened for row in rows),
            tuple(AudienceMember(UserId(row.user_id), row.deafened) for row in rows),
            None,
        )

    async def _credit_listeners(
        self,
        session_id: ListeningSessionId,
        playback_id: PlaybackRecordId,
        audience: AudienceState,
        observed_at: datetime,
        delta: float,
    ) -> None:
        audible = audience.audible_user_ids
        if not audible:
            return
        presences = (
            await self._session.scalars(
                select(_ListenerPresenceRow).where(
                    _ListenerPresenceRow.session_id == session_id,
                    _ListenerPresenceRow.user_id.in_(audible),
                    _ListenerPresenceRow.left_at.is_(None),
                    _ListenerPresenceRow.deafened.is_(False),
                )
            )
        ).all()
        chunk_start = observed_at - timedelta(seconds=delta)
        for presence in presences:
            heard_from = max(chunk_start, presence.joined_at)
            seconds = min(
                delta,
                max(0.0, (observed_at - heard_from).total_seconds()),
            )
            if seconds <= 0:
                continue
            await self._session.execute(
                insert(_PlaybackListenerRow)
                .values(
                    playback_id=playback_id,
                    user_id=presence.user_id,
                    audio_seconds=seconds,
                    first_heard_at=heard_from,
                    last_heard_at=observed_at,
                )
                .on_conflict_do_update(
                    index_elements=[
                        _PlaybackListenerRow.playback_id,
                        _PlaybackListenerRow.user_id,
                    ],
                    set_={
                        "audio_seconds": (_PlaybackListenerRow.audio_seconds + seconds),
                        "last_heard_at": observed_at,
                    },
                )
            )

    async def _confirm_audience(
        self,
        session_id: ListeningSessionId,
        audience: AudienceState,
        observed_at: datetime,
    ) -> None:
        if not audience.user_ids:
            return
        presences = (
            await self._session.scalars(
                select(_ListenerPresenceRow).where(
                    _ListenerPresenceRow.session_id == session_id,
                    _ListenerPresenceRow.user_id.in_(audience.user_ids),
                    _ListenerPresenceRow.left_at.is_(None),
                )
            )
        ).all()
        for presence in presences:
            if observed_at < presence.confirmed_at:
                raise ListeningError(ListeningErrorCode.AUDIENCE_SESSION_MISMATCH)
            presence.confirmed_at = observed_at


def _playback(row: _PlaybackRecordRow) -> PlaybackRecord:
    return PlaybackRecord(
        PlaybackRecordId(row.id),
        ListeningSessionId(row.session_id),
        TrackRequestId(row.request_id),
        row.started_at,
        row.audio_seconds,
        row.group_audio_seconds,
        row.ended_at,
        row.end_reason,
    )


def _presence(row: _ListenerPresenceRow) -> ListenerPresence:
    return ListenerPresence(
        ListenerPresenceId(row.id),
        ListeningSessionId(row.session_id),
        UserId(row.user_id),
        row.joined_at,
        row.confirmed_at,
        row.deafened,
        row.left_at,
    )


def _listener(row: _PlaybackListenerRow) -> PlaybackListener:
    return PlaybackListener(
        PlaybackRecordId(row.playback_id),
        UserId(row.user_id),
        row.audio_seconds,
        row.first_heard_at,
        row.last_heard_at,
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Relational storage for one player session aggregate."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.catalog.domain import (
    DiscoverySnapshotId,
    MediaKind,
    TrackId,
    TrackSourceId,
)
from nahoermaar.database.schema import Base
from nahoermaar.users.domain import UserId

from .domain import (
    ListeningSession,
    ListeningSessionId,
    MutationOutcome,
    OperationId,
    OperationReceipt,
    PlaybackCheckpoint,
    PlaybackIntent,
    PlayerAction,
    PlayerState,
    Queue,
    QueueEntry,
    QueueEntryId,
    QueueUndo,
    RadioCandidate,
    RadioCandidateId,
    RadioRun,
    RadioRunId,
    RadioSeed,
    RadioState,
    RequestOrigin,
    TrackRequest,
    TrackRequestId,
    UndoGroup,
    UndoId,
)

_SESSION_KEY = "default"


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_MEDIA_KIND = SqlEnum(
    MediaKind,
    name="player_media_kind",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_RADIO_STATE = SqlEnum(
    RadioState,
    name="radio_state",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_PLAYBACK_INTENT = SqlEnum(
    PlaybackIntent,
    name="playback_intent",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_PLAYER_ACTION = SqlEnum(
    PlayerAction,
    name="player_action",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_REQUEST_ORIGIN = SqlEnum(
    RequestOrigin,
    name="request_origin",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _ListeningSessionRow(Base):
    __tablename__ = "listening_sessions"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_non_negative"),
        CheckConstraint(
            "queue_revision >= 0 AND queue_revision <= revision",
            name="queue_revision_valid",
        ),
        CheckConstraint("volume >= 0 AND volume <= 1", name="volume_valid"),
        CheckConstraint(
            "crossfade_seconds IN (0, 3, 4, 5, 6, 7)",
            name="crossfade_supported",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_key: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    queue_revision: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[float] = mapped_column(Float)
    crossfade_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _RadioRunRow(Base):
    __tablename__ = "radio_runs"
    __table_args__ = (
        CheckConstraint(
            "(seed_kind = 'track' AND seed_track_source_id IS NOT NULL "
            "AND seed_discovery_snapshot_id IS NULL) OR "
            "(seed_kind = 'playlist' AND seed_track_source_id IS NULL "
            "AND seed_discovery_snapshot_id IS NOT NULL)",
            name="seed_identity_valid",
        ),
        CheckConstraint(
            "(state = 'loading' AND request_id IS NOT NULL) "
            "OR (state <> 'loading' AND request_id IS NULL)",
            name="request_state_valid",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="end_not_before_start",
        ),
        Index(
            "uq_radio_runs_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="RESTRICT"), index=True
    )
    seed_kind: Mapped[MediaKind] = mapped_column(_MEDIA_KIND)
    seed_track_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("track_sources.id", ondelete="RESTRICT")
    )
    seed_discovery_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discovery_snapshots.id", ondelete="RESTRICT")
    )
    initiated_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    state: Mapped[RadioState] = mapped_column(_RADIO_STATE)
    continuation: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    error: Mapped[str | None] = mapped_column(String(500))


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
        UniqueConstraint("id", "session_id", name="uq_track_requests_id_session"),
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


class _QueueEntryRow(Base):
    __tablename__ = "queue_entries"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("session_id", "position", name="uq_queue_entries_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_requests.id", ondelete="RESTRICT"), unique=True
    )
    position: Mapped[int] = mapped_column(Integer)


class _RadioCandidateRow(Base):
    __tablename__ = "radio_candidates"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("run_id", "position", name="uq_radio_candidates_position"),
        UniqueConstraint("run_id", "track_id", name="uq_radio_candidates_track"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("radio_runs.id", ondelete="CASCADE"), index=True
    )
    track_id: Mapped[UUID] = mapped_column(ForeignKey("tracks.id", ondelete="RESTRICT"))
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_sources.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(Integer)


class _RadioExclusionRow(Base):
    __tablename__ = "radio_exclusions"

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("radio_runs.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="RESTRICT"), primary_key=True
    )


class _PlaybackCheckpointRow(Base):
    __tablename__ = "player_checkpoints"
    __table_args__ = (
        CheckConstraint("position_seconds >= 0", name="position_non_negative"),
        CheckConstraint(
            "(intent = 'stopped' AND request_id IS NULL AND position_seconds = 0) "
            "OR (intent <> 'stopped' AND request_id IS NOT NULL)",
            name="intent_request_valid",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    intent: Mapped[PlaybackIntent] = mapped_column(_PLAYBACK_INTENT)
    request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("track_requests.id", ondelete="RESTRICT")
    )
    position_seconds: Mapped[float] = mapped_column(Float)


class _QueueUndoRow(Base):
    __tablename__ = "queue_undos"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class _QueueUndoGroupRow(Base):
    __tablename__ = "queue_undo_groups"

    undo_id: Mapped[UUID] = mapped_column(
        ForeignKey("queue_undos.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    previous_entry_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    next_entry_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class _QueueUndoEntryRow(Base):
    __tablename__ = "queue_undo_entries"

    undo_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    group_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_requests.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["undo_id", "group_position"],
            ["queue_undo_groups.undo_id", "queue_undo_groups.position"],
            name="fk_queue_undo_entries_group_queue_undo_groups",
            ondelete="CASCADE",
        ),
    )


class _OperationReceiptRow(Base):
    __tablename__ = "operation_receipts"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
    )

    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("listening_sessions.id", ondelete="CASCADE"), index=True
    )
    command_type: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    action: Mapped[PlayerAction] = mapped_column(_PLAYER_ACTION)
    added_count: Mapped[int] = mapped_column(Integer)
    removed_count: Mapped[int] = mapped_column(Integer)
    restored_count: Mapped[int] = mapped_column(Integer)
    skipped_count: Mapped[int] = mapped_column(Integer)
    undo_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    undo_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _OperationReceiptEntryRow(Base):
    __tablename__ = "operation_receipt_entries"

    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operation_receipts.operation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))


class SessionRepository:
    """Load and atomically persist the player aggregate."""

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

    async def load_default(self, now: datetime) -> PlayerState:
        row = await self._session.scalar(
            select(_ListeningSessionRow).where(
                _ListeningSessionRow.session_key == _SESSION_KEY
            )
        )
        if row is not None:
            loaded = await self.load(ListeningSessionId(row.id))
            if loaded is None:
                raise RuntimeError("Default player session disappeared.")
            return loaded
        state = PlayerState.empty(ListeningSessionId(uuid4()), now)
        await self.save(state)
        return state

    async def load(self, session_id: ListeningSessionId) -> PlayerState | None:
        row = await self._session.get(_ListeningSessionRow, session_id)
        if row is None:
            return None

        queue_rows = (
            await self._session.scalars(
                select(_QueueEntryRow)
                .where(_QueueEntryRow.session_id == session_id)
                .order_by(_QueueEntryRow.position)
            )
        ).all()
        checkpoint_row = await self._session.get(_PlaybackCheckpointRow, session_id)
        request_ids = [TrackRequestId(item.request_id) for item in queue_rows]
        if checkpoint_row is not None and checkpoint_row.request_id is not None:
            request_ids.append(TrackRequestId(checkpoint_row.request_id))
        requests = await self.requests(request_ids)

        entries = tuple(
            QueueEntry(
                QueueEntryId(item.id),
                session_id,
                _required_request(requests, TrackRequestId(item.request_id)),
                item.position,
            )
            for item in queue_rows
        )
        checkpoint = PlaybackCheckpoint(
            session_id,
            checkpoint_row.intent
            if checkpoint_row is not None
            else PlaybackIntent.STOPPED,
            (
                _required_request(
                    requests,
                    TrackRequestId(checkpoint_row.request_id),
                )
                if checkpoint_row is not None and checkpoint_row.request_id is not None
                else None
            ),
            checkpoint_row.position_seconds if checkpoint_row is not None else 0,
        )
        radio = await self._active_radio(session_id)
        session = ListeningSession(
            session_id,
            row.revision,
            row.queue_revision,
            row.channel_id,
            row.volume,
            row.crossfade_seconds,
            row.created_at,
            row.updated_at,
        )
        return PlayerState(
            session,
            Queue(session_id, row.queue_revision, entries),
            checkpoint,
            radio,
        )

    async def save(self, state: PlayerState) -> None:
        session = state.session
        await self._session.execute(
            insert(_ListeningSessionRow)
            .values(
                id=session.id,
                session_key=_SESSION_KEY,
                revision=session.revision,
                queue_revision=session.queue_revision,
                channel_id=session.channel_id,
                volume=session.volume,
                crossfade_seconds=session.crossfade_seconds,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
            .on_conflict_do_update(
                index_elements=[_ListeningSessionRow.id],
                set_={
                    "revision": session.revision,
                    "queue_revision": session.queue_revision,
                    "channel_id": session.channel_id,
                    "volume": session.volume,
                    "crossfade_seconds": session.crossfade_seconds,
                    "updated_at": session.updated_at,
                },
            )
        )

        if state.radio is not None:
            run = state.radio
            await self._session.execute(
                update(_RadioRunRow)
                .where(
                    _RadioRunRow.session_id == session.id,
                    _RadioRunRow.ended_at.is_(None),
                    _RadioRunRow.id != run.id,
                )
                .values(
                    state=RadioState.WAITING,
                    request_id=None,
                    ended_at=run.started_at,
                )
            )
            await self._session.execute(
                insert(_RadioRunRow)
                .values(
                    id=run.id,
                    session_id=run.session_id,
                    seed_kind=run.seed.kind,
                    seed_track_source_id=run.seed.track_source_id,
                    seed_discovery_snapshot_id=run.seed.discovery_snapshot_id,
                    initiated_by=run.initiated_by,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    generation=run.generation,
                    state=run.state,
                    continuation=run.continuation,
                    request_id=run.request_id,
                    error=run.error,
                )
                .on_conflict_do_update(
                    index_elements=[_RadioRunRow.id],
                    set_={
                        "ended_at": run.ended_at,
                        "state": run.state,
                        "continuation": run.continuation,
                        "request_id": run.request_id,
                        "error": run.error,
                    },
                )
            )

        requests = _requests_in(state)
        await self.add_requests(requests.values())

        await self._session.execute(
            delete(_QueueEntryRow).where(_QueueEntryRow.session_id == session.id)
        )
        if state.queue.entries:
            await self._session.execute(
                insert(_QueueEntryRow).values(
                    [
                        {
                            "id": entry.id,
                            "session_id": entry.session_id,
                            "request_id": entry.request.id,
                            "position": entry.position,
                        }
                        for entry in state.queue.entries
                    ]
                )
            )

        checkpoint = state.checkpoint
        await self._session.execute(
            insert(_PlaybackCheckpointRow)
            .values(
                session_id=session.id,
                intent=checkpoint.intent,
                request_id=checkpoint.request.id if checkpoint.request else None,
                position_seconds=checkpoint.position_seconds,
            )
            .on_conflict_do_update(
                index_elements=[_PlaybackCheckpointRow.session_id],
                set_={
                    "intent": checkpoint.intent,
                    "request_id": (
                        checkpoint.request.id
                        if checkpoint.request is not None
                        else None
                    ),
                    "position_seconds": checkpoint.position_seconds,
                },
            )
        )
        await self._save_radio_relations(state.radio)

    async def undo(self, undo_id: UndoId) -> QueueUndo | None:
        row = await self._session.get(_QueueUndoRow, undo_id)
        if row is None:
            return None
        groups = (
            await self._session.scalars(
                select(_QueueUndoGroupRow)
                .where(_QueueUndoGroupRow.undo_id == undo_id)
                .order_by(_QueueUndoGroupRow.position)
            )
        ).all()
        items = (
            await self._session.scalars(
                select(_QueueUndoEntryRow)
                .where(_QueueUndoEntryRow.undo_id == undo_id)
                .order_by(
                    _QueueUndoEntryRow.group_position,
                    _QueueUndoEntryRow.position,
                )
            )
        ).all()
        requests = await self.requests(
            TrackRequestId(item.request_id) for item in items
        )
        by_group: dict[int, list[QueueEntry]] = {}
        for item in items:
            request = _required_request(requests, TrackRequestId(item.request_id))
            by_group.setdefault(item.group_position, []).append(
                QueueEntry(
                    QueueEntryId(item.entry_id),
                    ListeningSessionId(row.session_id),
                    request,
                    item.position,
                )
            )
        return QueueUndo(
            UndoId(row.id),
            ListeningSessionId(row.session_id),
            UserId(row.actor_id),
            row.created_at,
            row.expires_at,
            tuple(
                UndoGroup(
                    tuple(by_group.get(group.position, ())),
                    (
                        QueueEntryId(group.previous_entry_id)
                        if group.previous_entry_id is not None
                        else None
                    ),
                    (
                        QueueEntryId(group.next_entry_id)
                        if group.next_entry_id is not None
                        else None
                    ),
                )
                for group in groups
            ),
        )

    async def save_undo(self, undo: QueueUndo) -> None:
        await self.add_requests(
            entry.request for group in undo.groups for entry in group.entries
        )
        await self._session.execute(
            insert(_QueueUndoRow).values(
                id=undo.id,
                session_id=undo.session_id,
                actor_id=undo.actor_id,
                created_at=undo.created_at,
                expires_at=undo.expires_at,
            )
        )
        for group_position, group in enumerate(undo.groups):
            await self._session.execute(
                insert(_QueueUndoGroupRow).values(
                    undo_id=undo.id,
                    position=group_position,
                    previous_entry_id=group.previous_id,
                    next_entry_id=group.next_id,
                )
            )
            if group.entries:
                await self._session.execute(
                    insert(_QueueUndoEntryRow).values(
                        [
                            {
                                "undo_id": undo.id,
                                "group_position": group_position,
                                "position": entry.position,
                                "entry_id": entry.id,
                                "request_id": entry.request.id,
                            }
                            for entry in group.entries
                        ]
                    )
                )

    async def consume_undo(self, undo_id: UndoId) -> None:
        await self._session.execute(
            delete(_QueueUndoRow).where(_QueueUndoRow.id == undo_id)
        )

    async def receipt(self, operation_id: OperationId) -> OperationReceipt | None:
        row = await self._session.get(_OperationReceiptRow, operation_id)
        if row is None:
            return None
        entry_rows = (
            await self._session.scalars(
                select(_OperationReceiptEntryRow)
                .where(_OperationReceiptEntryRow.operation_id == operation_id)
                .order_by(_OperationReceiptEntryRow.position)
            )
        ).all()
        outcome = MutationOutcome(
            row.action,
            row.added_count,
            row.removed_count,
            row.restored_count,
            row.skipped_count,
            tuple(QueueEntryId(item.entry_id) for item in entry_rows),
            UndoId(row.undo_id) if row.undo_id is not None else None,
            row.undo_expires_at,
        )
        return OperationReceipt(
            OperationId(row.operation_id),
            ListeningSessionId(row.session_id),
            row.command_type,
            row.fingerprint,
            row.created_at,
            row.expires_at,
            outcome,
        )

    async def save_receipt(self, receipt: OperationReceipt) -> None:
        outcome = receipt.outcome
        await self._session.execute(
            insert(_OperationReceiptRow).values(
                operation_id=receipt.operation_id,
                session_id=receipt.session_id,
                command_type=receipt.command_type,
                fingerprint=receipt.fingerprint,
                created_at=receipt.created_at,
                expires_at=receipt.expires_at,
                action=outcome.action,
                added_count=outcome.added_count,
                removed_count=outcome.removed_count,
                restored_count=outcome.restored_count,
                skipped_count=outcome.skipped_count,
                undo_id=outcome.undo_id,
                undo_expires_at=outcome.undo_expires_at,
            )
        )
        if outcome.entry_ids:
            await self._session.execute(
                insert(_OperationReceiptEntryRow).values(
                    [
                        {
                            "operation_id": receipt.operation_id,
                            "position": position,
                            "entry_id": entry_id,
                        }
                        for position, entry_id in enumerate(outcome.entry_ids)
                    ]
                )
            )

    async def prune(self, now: datetime) -> tuple[int, int]:
        undos = await self._session.execute(
            delete(_QueueUndoRow)
            .where(_QueueUndoRow.expires_at <= now)
            .returning(_QueueUndoRow.id)
        )
        receipts = await self._session.execute(
            delete(_OperationReceiptRow)
            .where(_OperationReceiptRow.expires_at <= now)
            .returning(_OperationReceiptRow.operation_id)
        )
        return len(undos.all()), len(receipts.all())

    async def _active_radio(
        self,
        session_id: ListeningSessionId,
    ) -> RadioRun | None:
        row = await self._session.scalar(
            select(_RadioRunRow).where(
                _RadioRunRow.session_id == session_id,
                _RadioRunRow.ended_at.is_(None),
            )
        )
        if row is None:
            return None
        candidate_rows = (
            await self._session.scalars(
                select(_RadioCandidateRow)
                .where(_RadioCandidateRow.run_id == row.id)
                .order_by(_RadioCandidateRow.position)
            )
        ).all()
        excluded = (
            await self._session.scalars(
                select(_RadioExclusionRow.track_id).where(
                    _RadioExclusionRow.run_id == row.id
                )
            )
        ).all()
        seed = RadioSeed(
            row.seed_kind,
            (
                TrackSourceId(row.seed_track_source_id)
                if row.seed_track_source_id is not None
                else None
            ),
            (
                DiscoverySnapshotId(row.seed_discovery_snapshot_id)
                if row.seed_discovery_snapshot_id is not None
                else None
            ),
        )
        return RadioRun(
            RadioRunId(row.id),
            session_id,
            seed,
            UserId(row.initiated_by),
            row.started_at,
            row.generation,
            row.state,
            row.continuation,
            row.request_id,
            tuple(
                RadioCandidate(
                    RadioCandidateId(candidate.id),
                    RadioRunId(row.id),
                    TrackId(candidate.track_id),
                    TrackSourceId(candidate.source_id),
                    candidate.position,
                )
                for candidate in candidate_rows
            ),
            frozenset(TrackId(track_id) for track_id in excluded),
            row.error,
            row.ended_at,
        )

    async def _save_radio_relations(self, run: RadioRun | None) -> None:
        if run is None:
            return
        await self._session.execute(
            delete(_RadioCandidateRow).where(_RadioCandidateRow.run_id == run.id)
        )
        await self._session.execute(
            delete(_RadioExclusionRow).where(_RadioExclusionRow.run_id == run.id)
        )
        if run.candidates:
            await self._session.execute(
                insert(_RadioCandidateRow).values(
                    [
                        {
                            "id": candidate.id,
                            "run_id": candidate.run_id,
                            "track_id": candidate.track_id,
                            "source_id": candidate.source_id,
                            "position": candidate.position,
                        }
                        for candidate in run.candidates
                    ]
                )
            )
        if run.excluded_track_ids:
            await self._session.execute(
                insert(_RadioExclusionRow).values(
                    [
                        {"run_id": run.id, "track_id": track_id}
                        for track_id in run.excluded_track_ids
                    ]
                )
            )


def _requests_in(state: PlayerState) -> dict[TrackRequestId, TrackRequest]:
    requests = {entry.request.id: entry.request for entry in state.queue.entries}
    if state.checkpoint.request is not None:
        requests[state.checkpoint.request.id] = state.checkpoint.request
    return requests


def _required_request(
    requests: dict[TrackRequestId, TrackRequest],
    request_id: TrackRequestId,
) -> TrackRequest:
    try:
        return requests[request_id]
    except KeyError as error:
        raise RuntimeError(f"Track request {request_id} is missing.") from error

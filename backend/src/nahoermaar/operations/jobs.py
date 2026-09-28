# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persisted lifecycle records for bounded application background jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    and_,
    delete,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import UserId

from .incidents import (
    IncidentKind,
    IncidentService,
    IncidentSeverity,
    IncidentTrigger,
)

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)
HISTORY_RETENTION_DAYS = 30
_HISTORY_RETENTION = timedelta(days=HISTORY_RETENTION_DAYS)


class JobId(StrEnum):
    CATALOG_MAINTENANCE = "catalog-maintenance"
    HOUSEKEEPING = "housekeeping"


class JobTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class JobRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    NEEDS_ATTENTION = "needs_attention"


class JobRunDetailKind(StrEnum):
    TRACK_METADATA = "track_metadata"
    DISCOVERY_REFRESH = "discovery_refresh"
    DATA_CLEANUP = "data_cleanup"


class JobRunDetailOutcome(StrEnum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class JobRunDetail:
    kind: JobRunDetailKind
    outcome: JobRunDetailOutcome
    label: str
    summary: str
    affected_count: int = 0
    subject_id: str | None = None
    source: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class JobRun:
    id: UUID
    job_id: JobId
    trigger: JobTrigger
    status: JobRunStatus
    requested_by: UserId | None
    requested_count: int
    candidate_count: int
    processed_count: int
    changed_count: int
    failure_count: int
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    details: tuple[JobRunDetail, ...] = ()

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.started_at).total_seconds())


@dataclass(frozen=True, slots=True)
class JobRunCursor:
    started_at: datetime
    run_id: UUID


@dataclass(frozen=True, slots=True)
class JobRunPage:
    entries: tuple[JobRun, ...]
    next_cursor: JobRunCursor | None


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_JOB_ID = SqlEnum(
    JobId,
    name="background_job_id",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_TRIGGER = SqlEnum(
    JobTrigger,
    name="background_job_trigger",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_STATUS = SqlEnum(
    JobRunStatus,
    name="background_job_run_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_DETAIL_KIND = SqlEnum(
    JobRunDetailKind,
    name="background_job_run_detail_kind",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_DETAIL_OUTCOME = SqlEnum(
    JobRunDetailOutcome,
    name="background_job_run_detail_outcome",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _JobRunRow(Base):
    __tablename__ = "background_job_runs"
    __table_args__ = (
        CheckConstraint("requested_count >= 0", name="requested_count_non_negative"),
        CheckConstraint("candidate_count >= 0", name="candidate_count_non_negative"),
        CheckConstraint("processed_count >= 0", name="processed_count_non_negative"),
        CheckConstraint("changed_count >= 0", name="changed_count_non_negative"),
        CheckConstraint("failure_count >= 0", name="failure_count_non_negative"),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status <> 'running' AND finished_at IS NOT NULL)",
            name="finished_state_valid",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="finish_not_before_start",
        ),
        Index("ix_background_job_runs_job_started", "job_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[JobId] = mapped_column(_JOB_ID)
    trigger: Mapped[JobTrigger] = mapped_column(_TRIGGER)
    status: Mapped[JobRunStatus] = mapped_column(_STATUS, index=True)
    requested_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    requested_count: Mapped[int] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer)
    processed_count: Mapped[int] = mapped_column(Integer)
    changed_count: Mapped[int] = mapped_column(Integer)
    failure_count: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))


class _JobRunDetailRow(Base):
    __tablename__ = "background_job_run_details"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("affected_count >= 0", name="affected_count_non_negative"),
        UniqueConstraint("run_id", "position"),
        Index("ix_background_job_run_details_run_position", "run_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("background_job_runs.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[JobRunDetailKind] = mapped_column(_DETAIL_KIND)
    outcome: Mapped[JobRunDetailOutcome] = mapped_column(_DETAIL_OUTCOME)
    label: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(String(1000))
    affected_count: Mapped[int] = mapped_column(Integer)
    subject_id: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(120))


class JobRunRepository:
    """Persist job lifecycle facts inside an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, run: JobRun) -> None:
        self._session.add(
            _JobRunRow(
                id=run.id,
                job_id=run.job_id,
                trigger=run.trigger,
                status=run.status,
                requested_by=run.requested_by,
                requested_count=run.requested_count,
                candidate_count=run.candidate_count,
                processed_count=run.processed_count,
                changed_count=run.changed_count,
                failure_count=run.failure_count,
                started_at=run.started_at,
                finished_at=run.finished_at,
                error_code=run.error_code,
            )
        )

    async def finish(self, run: JobRun) -> bool:
        updated = await self._session.execute(
            update(_JobRunRow)
            .where(
                _JobRunRow.id == run.id,
                _JobRunRow.status == JobRunStatus.RUNNING,
            )
            .values(
                status=run.status,
                candidate_count=run.candidate_count,
                processed_count=run.processed_count,
                changed_count=run.changed_count,
                failure_count=run.failure_count,
                finished_at=run.finished_at,
                error_code=run.error_code,
            )
            .returning(_JobRunRow.id)
        )
        return updated.scalar_one_or_none() is not None

    def add_details(self, run_id: UUID, details: tuple[JobRunDetail, ...]) -> None:
        self._session.add_all(
            _JobRunDetailRow(
                id=uuid4(),
                run_id=run_id,
                position=position,
                kind=detail.kind,
                outcome=detail.outcome,
                label=detail.label[:500],
                summary=detail.summary[:1000],
                affected_count=detail.affected_count,
                subject_id=detail.subject_id[:500] if detail.subject_id else None,
                source=detail.source[:100] if detail.source else None,
                error_code=detail.error_code[:120] if detail.error_code else None,
            )
            for position, detail in enumerate(details)
        )

    async def recent(self, limit: int = 20) -> tuple[JobRun, ...]:
        rows = tuple(
            await self._session.scalars(
                select(_JobRunRow)
                .order_by(_JobRunRow.started_at.desc(), _JobRunRow.id.desc())
                .limit(limit)
            )
        )
        if not rows:
            return ()
        detail_rows = await self._session.scalars(
            select(_JobRunDetailRow)
            .where(_JobRunDetailRow.run_id.in_(row.id for row in rows))
            .order_by(_JobRunDetailRow.run_id, _JobRunDetailRow.position)
        )
        details_by_run: dict[UUID, list[JobRunDetail]] = {}
        for detail_row in detail_rows:
            details_by_run.setdefault(detail_row.run_id, []).append(
                _to_detail(detail_row)
            )
        return tuple(
            _to_run(row, tuple(details_by_run.get(row.id, ()))) for row in rows
        )

    async def page(
        self,
        *,
        limit: int,
        cursor: JobRunCursor | None = None,
        job_id: JobId | None = None,
        status: JobRunStatus | None = None,
    ) -> JobRunPage:
        query = select(_JobRunRow)
        if cursor is not None:
            query = query.where(
                or_(
                    _JobRunRow.started_at < cursor.started_at,
                    and_(
                        _JobRunRow.started_at == cursor.started_at,
                        _JobRunRow.id < cursor.run_id,
                    ),
                )
            )
        if job_id is not None:
            query = query.where(_JobRunRow.job_id == job_id)
        if status is not None:
            query = query.where(_JobRunRow.status == status)
        rows = tuple(
            await self._session.scalars(
                query.order_by(
                    _JobRunRow.started_at.desc(), _JobRunRow.id.desc()
                ).limit(limit + 1)
            )
        )
        entries = tuple(_to_run(row) for row in rows[:limit])
        next_cursor = (
            JobRunCursor(entries[-1].started_at, entries[-1].id)
            if len(rows) > limit and entries
            else None
        )
        return JobRunPage(entries, next_cursor)

    async def get(self, run_id: UUID) -> JobRun | None:
        row = await self._session.get(_JobRunRow, run_id)
        if row is None:
            return None
        detail_rows = await self._session.scalars(
            select(_JobRunDetailRow)
            .where(_JobRunDetailRow.run_id == run_id)
            .order_by(_JobRunDetailRow.position)
        )
        return _to_run(row, tuple(_to_detail(detail) for detail in detail_rows))

    async def latest_health_run(self, job_id: JobId) -> JobRun | None:
        row = await self._session.scalar(
            select(_JobRunRow)
            .where(
                _JobRunRow.job_id == job_id,
                _JobRunRow.status.in_(
                    (
                        JobRunStatus.SUCCEEDED,
                        JobRunStatus.PARTIAL,
                        JobRunStatus.FAILED,
                    )
                ),
            )
            .order_by(_JobRunRow.started_at.desc(), _JobRunRow.id.desc())
            .limit(1)
        )
        return _to_run(row) if row is not None else None

    async def interrupt_running(self, finished_at: datetime) -> tuple[JobRun, ...]:
        rows = tuple(
            await self._session.scalars(
                select(_JobRunRow).where(_JobRunRow.status == JobRunStatus.RUNNING)
            )
        )
        for row in rows:
            row.status = JobRunStatus.FAILED
            row.finished_at = max(row.started_at, finished_at)
            row.error_code = "job_interrupted"
            row.failure_count = max(1, row.failure_count)
        return tuple(_to_run(row) for row in rows)

    async def purge_before(self, cutoff: datetime, limit: int) -> int:
        identifiers = tuple(
            await self._session.scalars(
                select(_JobRunRow.id)
                .where(
                    _JobRunRow.status != JobRunStatus.RUNNING,
                    _JobRunRow.finished_at < cutoff,
                )
                .order_by(_JobRunRow.finished_at, _JobRunRow.id)
                .limit(limit)
            )
        )
        if not identifiers:
            return 0
        removed = await self._session.execute(
            delete(_JobRunRow)
            .where(_JobRunRow.id.in_(identifiers))
            .returning(_JobRunRow.id)
        )
        return len(removed.all())


class JobRunService:
    """Record bounded jobs and project their failures into incidents."""

    __slots__ = ("_clock", "_incidents", "_units")

    def __init__(
        self,
        units: UnitFactory,
        incidents: IncidentService,
        *,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._units = units
        self._incidents = incidents
        self._clock = clock

    async def reconcile_interrupted_runs(self) -> None:
        now = self._clock().astimezone(UTC)
        async with self._units() as work:
            interrupted = await JobRunRepository(work.session).interrupt_running(now)
            await work.commit()
        for run in interrupted:
            await self._record_incident(
                run,
                kind=IncidentKind.FAILED,
                severity=IncidentSeverity.ERROR,
                error_code="job_interrupted",
            )

    async def start_run(
        self,
        job_id: JobId | str,
        trigger: JobTrigger | str,
        requested_count: int,
        requested_by: UserId | None = None,
    ) -> JobRun:
        run = JobRun(
            id=uuid4(),
            job_id=JobId(job_id),
            trigger=JobTrigger(trigger),
            status=JobRunStatus.RUNNING,
            requested_by=requested_by,
            requested_count=requested_count,
            candidate_count=0,
            processed_count=0,
            changed_count=0,
            failure_count=0,
            started_at=self._clock().astimezone(UTC),
        )
        async with self._units() as work:
            JobRunRepository(work.session).add(run)
            await work.commit()
        _LOGGER.info(
            "jobs.run_started job=%s run_id=%s trigger=%s requested=%d actor_id=%s",
            run.job_id,
            run.id,
            run.trigger,
            run.requested_count,
            run.requested_by,
        )
        return run

    async def finish_run(
        self,
        run: JobRun,
        *,
        candidate_count: int,
        processed_count: int,
        changed_count: int,
        failure_count: int = 0,
        error_code: str | None = None,
        details: tuple[JobRunDetail, ...] = (),
    ) -> JobRun:
        status = JobRunStatus.PARTIAL if failure_count else JobRunStatus.SUCCEEDED
        finished = replace(
            run,
            status=status,
            candidate_count=candidate_count,
            processed_count=processed_count,
            changed_count=changed_count,
            failure_count=failure_count,
            finished_at=self._clock().astimezone(UTC),
            error_code=error_code,
            details=details,
        )
        previous = await self._persist_finished(finished)
        if status is JobRunStatus.PARTIAL:
            await self._record_incident(
                finished,
                kind=IncidentKind.FAILED,
                severity=IncidentSeverity.WARNING,
                error_code=error_code or f"{run.job_id.value}_partial",
            )
        elif previous is not None and previous.status in {
            JobRunStatus.PARTIAL,
            JobRunStatus.FAILED,
        }:
            await self._record_incident(
                finished,
                kind=IncidentKind.RECOVERED,
                severity=IncidentSeverity.WARNING,
                error_code=previous.error_code or f"{run.job_id.value}_failed",
            )
        _LOGGER.info(
            "jobs.run_finished job=%s run_id=%s status=%s candidates=%d "
            "processed=%d changed=%d failures=%d",
            finished.job_id,
            finished.id,
            finished.status,
            finished.candidate_count,
            finished.processed_count,
            finished.changed_count,
            finished.failure_count,
        )
        return finished

    async def fail_run(self, run: JobRun, error_code: str) -> JobRun:
        failed = replace(
            run,
            status=JobRunStatus.FAILED,
            failure_count=max(1, run.failure_count),
            finished_at=self._clock().astimezone(UTC),
            error_code=error_code[:120],
        )
        await self._persist_finished(failed)
        await self._record_incident(
            failed,
            kind=IncidentKind.FAILED,
            severity=IncidentSeverity.ERROR,
            error_code=error_code,
        )
        _LOGGER.error(
            "jobs.run_failed job=%s run_id=%s error=%s",
            failed.job_id,
            failed.id,
            failed.error_code,
        )
        return failed

    async def cancel_run(self, run: JobRun) -> JobRun:
        """Finish a run cancelled by an orderly application shutdown."""
        cancelled = replace(
            run,
            status=JobRunStatus.CANCELLED,
            finished_at=self._clock().astimezone(UTC),
            error_code="job_cancelled",
        )
        await self._persist_finished(cancelled)
        _LOGGER.info(
            "jobs.run_cancelled job=%s run_id=%s",
            cancelled.job_id,
            cancelled.id,
        )
        return cancelled

    async def recent(self, limit: int = 20) -> tuple[JobRun, ...]:
        async with self._units() as work:
            return await JobRunRepository(work.session).recent(limit)

    async def health(self, job_ids: tuple[JobId, ...]) -> dict[JobId, JobHealth]:
        async with self._units() as work:
            repository = JobRunRepository(work.session)
            return {
                job_id: _health(await repository.latest_health_run(job_id))
                for job_id in job_ids
            }

    async def runs(
        self,
        *,
        limit: int,
        cursor: JobRunCursor | None = None,
        job_id: JobId | None = None,
        status: JobRunStatus | None = None,
    ) -> JobRunPage:
        async with self._units() as work:
            return await JobRunRepository(work.session).page(
                limit=limit,
                cursor=cursor,
                job_id=job_id,
                status=status,
            )

    async def run(self, run_id: UUID) -> JobRun | None:
        async with self._units() as work:
            return await JobRunRepository(work.session).get(run_id)

    async def purge(self, now: datetime, limit: int) -> int:
        async with self._units() as work:
            removed = await JobRunRepository(work.session).purge_before(
                now.astimezone(UTC) - _HISTORY_RETENTION,
                limit,
            )
            await work.commit()
        return removed

    async def _persist_finished(self, run: JobRun) -> JobRun | None:
        async with self._units() as work:
            repository = JobRunRepository(work.session)
            previous = await repository.latest_health_run(run.job_id)
            if not await repository.finish(run):
                raise RuntimeError(f"Job run {run.id} is no longer running.")
            repository.add_details(run.id, run.details)
            await work.commit()
        return previous

    async def _record_incident(
        self,
        run: JobRun,
        *,
        kind: IncidentKind,
        severity: IncidentSeverity,
        error_code: str,
    ) -> None:
        await self._incidents.record(
            severity=severity,
            kind=kind,
            component="jobs",
            error_code=error_code,
            actor_id=run.requested_by,
            operation_type=f"job.{run.job_id.value}",
            correlation_id=run.id,
            trigger=(
                IncidentTrigger.USER
                if run.trigger is JobTrigger.MANUAL
                else IncidentTrigger.SYSTEM
            ),
            occurred_at=run.finished_at,
        )


def _health(run: JobRun | None) -> JobHealth:
    if run is None:
        return JobHealth.UNKNOWN
    if run.status is JobRunStatus.SUCCEEDED:
        return JobHealth.HEALTHY
    return JobHealth.NEEDS_ATTENTION


def _to_run(row: _JobRunRow, details: tuple[JobRunDetail, ...] = ()) -> JobRun:
    return JobRun(
        id=row.id,
        job_id=row.job_id,
        trigger=row.trigger,
        status=row.status,
        requested_by=UserId(row.requested_by) if row.requested_by is not None else None,
        requested_count=row.requested_count,
        candidate_count=row.candidate_count,
        processed_count=row.processed_count,
        changed_count=row.changed_count,
        failure_count=row.failure_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        details=details,
    )


def _to_detail(row: _JobRunDetailRow) -> JobRunDetail:
    return JobRunDetail(
        kind=row.kind,
        outcome=row.outcome,
        label=row.label,
        summary=row.summary,
        affected_count=row.affected_count,
        subject_id=row.subject_id,
        source=row.source,
        error_code=row.error_code,
    )

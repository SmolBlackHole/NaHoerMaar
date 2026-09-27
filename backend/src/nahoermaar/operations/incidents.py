# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Short-lived structured incidents for operator diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from uuid import UUID, uuid5

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    String,
    Uuid,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import UserId

RETENTION_DAYS = 14
_RETENTION = timedelta(days=RETENTION_DAYS)

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]


class IncidentSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IncidentKind(StrEnum):
    REJECTED = "rejected"
    FAILED = "failed"
    RETRY = "retry"
    RECOVERED = "recovered"


class IncidentTrigger(StrEnum):
    USER = "user"
    SYSTEM = "system"


class IncidentPeriod(StrEnum):
    HOURS_24 = "24h"
    DAYS_7 = "7d"
    DAYS_14 = "14d"


@dataclass(frozen=True, slots=True)
class Incident:
    id: UUID
    occurred_at: datetime
    severity: IncidentSeverity
    kind: IncidentKind
    component: str
    error_code: str
    actor_id: UserId | None
    operation_type: str
    correlation_id: UUID
    trigger: IncidentTrigger


@dataclass(frozen=True, slots=True)
class IncidentIdentity:
    user_id: UserId
    display_name: str | None
    discord_username: str | None


@dataclass(frozen=True, slots=True)
class IncidentTotals:
    warnings: int
    errors: int
    critical: int
    user_triggered: int
    system_triggered: int
    rejected: int
    failed: int
    retries: int
    recoveries: int


@dataclass(frozen=True, slots=True)
class IncidentErrorCount:
    component: str
    error_code: str
    count: int


@dataclass(frozen=True, slots=True)
class IncidentOperationCount:
    operation_type: str
    warnings: int
    errors: int

    @property
    def count(self) -> int:
        return self.warnings + self.errors


@dataclass(frozen=True, slots=True)
class AssociatedUserCount:
    identity: IncidentIdentity
    rejected_commands: int


@dataclass(frozen=True, slots=True)
class IncidentReport:
    period: IncidentPeriod
    started_at: datetime
    ended_at: datetime
    retention_days: int
    recorded_since: datetime | None
    totals: IncidentTotals
    common_errors: tuple[IncidentErrorCount, ...]
    operations: tuple[IncidentOperationCount, ...]
    associated_users: tuple[AssociatedUserCount, ...]
    current_failure_free_seconds: float | None
    longest_failure_free_seconds: float | None
    recent: tuple[Incident, ...]


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_SEVERITY = SqlEnum(
    IncidentSeverity,
    name="incident_severity",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_KIND = SqlEnum(
    IncidentKind,
    name="incident_kind",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_TRIGGER = SqlEnum(
    IncidentTrigger,
    name="incident_trigger",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _IncidentRow(Base):
    __tablename__ = "operational_incidents"
    __table_args__ = (
        Index("ix_operational_incidents_component_code", "component", "error_code"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    severity: Mapped[IncidentSeverity] = mapped_column(_SEVERITY)
    kind: Mapped[IncidentKind] = mapped_column(_KIND)
    component: Mapped[str] = mapped_column(String(80))
    error_code: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    operation_type: Mapped[str] = mapped_column(String(160), index=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    trigger: Mapped[IncidentTrigger] = mapped_column(_TRIGGER)


class IncidentRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, incident: Incident) -> bool:
        value: UUID | None = await self._session.scalar(
            insert(_IncidentRow)
            .values(
                id=incident.id,
                occurred_at=incident.occurred_at,
                severity=incident.severity,
                kind=incident.kind,
                component=incident.component,
                error_code=incident.error_code,
                actor_id=incident.actor_id,
                operation_type=incident.operation_type,
                correlation_id=incident.correlation_id,
                trigger=incident.trigger,
            )
            .on_conflict_do_nothing(index_elements=[_IncidentRow.id])
            .returning(_IncidentRow.id)
        )
        return value is not None

    async def purge_before(self, cutoff: datetime) -> None:
        await self._session.execute(
            delete(_IncidentRow).where(_IncidentRow.occurred_at < cutoff)
        )

    async def between(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[Incident, ...]:
        rows = (
            (
                await self._session.execute(
                    select(_IncidentRow)
                    .where(
                        _IncidentRow.occurred_at >= started_at,
                        _IncidentRow.occurred_at <= ended_at,
                    )
                    .order_by(_IncidentRow.occurred_at, _IncidentRow.id)
                )
            )
            .scalars()
            .all()
        )
        return tuple(_incident(row) for row in rows)

    async def identities(
        self,
        user_ids: set[UserId],
    ) -> dict[UserId, IncidentIdentity]:
        if not user_ids:
            return {}
        users = Base.metadata.tables["users"]
        profiles = Base.metadata.tables["user_profiles"]
        discord = Base.metadata.tables["discord_identities"]
        rows = (
            await self._session.execute(
                select(
                    users.c.id,
                    profiles.c.display_name,
                    discord.c.username,
                )
                .select_from(
                    users.outerjoin(
                        profiles, profiles.c.user_id == users.c.id
                    ).outerjoin(discord, discord.c.user_id == users.c.id)
                )
                .where(users.c.id.in_(user_ids))
            )
        ).all()
        return {
            UserId(row.id): IncidentIdentity(
                UserId(row.id),
                row.display_name,
                row.username,
            )
            for row in rows
        }


class IncidentService:
    """Record and summarize a bounded operational incident history."""

    __slots__ = ("_clock", "_units")

    def __init__(
        self, units: UnitFactory, *, clock: Clock = lambda: datetime.now(UTC)
    ) -> None:
        self._units = units
        self._clock = clock

    async def record(
        self,
        *,
        severity: IncidentSeverity,
        kind: IncidentKind,
        component: str,
        error_code: str,
        actor_id: UUID | None,
        operation_type: str,
        correlation_id: UUID,
        trigger: IncidentTrigger,
        occurred_at: datetime | None = None,
    ) -> bool:
        now = self._clock().astimezone(UTC)
        timestamp = (occurred_at or now).astimezone(UTC)
        retention_cutoff = now - _RETENTION
        if timestamp < retention_cutoff:
            async with self._units() as work:
                await IncidentRepository(work.session).purge_before(retention_cutoff)
                await work.commit()
            return False
        incident = Incident(
            id=uuid5(correlation_id, f"{kind.value}:{error_code}"),
            occurred_at=timestamp,
            severity=severity,
            kind=kind,
            component=_bounded(component, 80),
            error_code=_bounded(error_code, 120),
            actor_id=UserId(actor_id) if actor_id is not None else None,
            operation_type=_bounded(operation_type, 160),
            correlation_id=correlation_id,
            trigger=trigger,
        )
        async with self._units() as work:
            repository = IncidentRepository(work.session)
            await repository.purge_before(retention_cutoff)
            inserted = await repository.add(incident)
            await work.commit()
        return inserted

    async def report(self, period: IncidentPeriod) -> IncidentReport:
        ended_at = self._clock().astimezone(UTC)
        started_at = ended_at - _period_delta(period)
        retention_cutoff = ended_at - _RETENTION
        if started_at < retention_cutoff:
            started_at = retention_cutoff
        async with self._units() as work:
            repository = IncidentRepository(work.session)
            await repository.purge_before(retention_cutoff)
            incidents = await repository.between(started_at, ended_at)
            identities = await repository.identities(
                {
                    incident.actor_id
                    for incident in incidents
                    if incident.actor_id is not None
                }
            )
            await work.commit()
        return _report(period, started_at, ended_at, incidents, identities)


def _incident(row: _IncidentRow) -> Incident:
    return Incident(
        row.id,
        row.occurred_at,
        row.severity,
        row.kind,
        row.component,
        row.error_code,
        UserId(row.actor_id) if row.actor_id is not None else None,
        row.operation_type,
        row.correlation_id,
        row.trigger,
    )


def _report(
    period: IncidentPeriod,
    started_at: datetime,
    ended_at: datetime,
    incidents: tuple[Incident, ...],
    identities: dict[UserId, IncidentIdentity],
) -> IncidentReport:
    severity = Counter(incident.severity for incident in incidents)
    trigger = Counter(incident.trigger for incident in incidents)
    kind = Counter(incident.kind for incident in incidents)
    errors = Counter(
        (incident.component, incident.error_code) for incident in incidents
    )
    operations: dict[str, Counter[str]] = {}
    rejected_users: Counter[UserId] = Counter()
    for incident in incidents:
        operation = operations.setdefault(incident.operation_type, Counter())
        operation[
            "warnings" if incident.severity is IncidentSeverity.WARNING else "errors"
        ] += 1
        if incident.kind is IncidentKind.REJECTED and incident.actor_id is not None:
            rejected_users[incident.actor_id] += 1
    failure_free, longest = _failure_free(incidents, ended_at)
    return IncidentReport(
        period=period,
        started_at=started_at,
        ended_at=ended_at,
        retention_days=RETENTION_DAYS,
        recorded_since=incidents[0].occurred_at if incidents else None,
        totals=IncidentTotals(
            warnings=severity[IncidentSeverity.WARNING],
            errors=severity[IncidentSeverity.ERROR],
            critical=severity[IncidentSeverity.CRITICAL],
            user_triggered=trigger[IncidentTrigger.USER],
            system_triggered=trigger[IncidentTrigger.SYSTEM],
            rejected=kind[IncidentKind.REJECTED],
            failed=kind[IncidentKind.FAILED],
            retries=kind[IncidentKind.RETRY],
            recoveries=kind[IncidentKind.RECOVERED],
        ),
        common_errors=tuple(
            IncidentErrorCount(component, error_code, count)
            for (component, error_code), count in errors.most_common(8)
        ),
        operations=tuple(
            sorted(
                (
                    IncidentOperationCount(name, counts["warnings"], counts["errors"])
                    for name, counts in operations.items()
                ),
                key=lambda item: (-item.count, item.operation_type),
            )[:8]
        ),
        associated_users=tuple(
            AssociatedUserCount(
                identities.get(user_id, IncidentIdentity(user_id, None, None)),
                count,
            )
            for user_id, count in rejected_users.most_common(8)
        ),
        current_failure_free_seconds=failure_free,
        longest_failure_free_seconds=longest,
        recent=tuple(reversed(incidents[-50:])),
    )


def _failure_free(
    incidents: tuple[Incident, ...],
    ended_at: datetime,
) -> tuple[float | None, float | None]:
    if not incidents:
        return None, None
    failures = [
        incident.occurred_at
        for incident in incidents
        if incident.severity in {IncidentSeverity.ERROR, IncidentSeverity.CRITICAL}
    ]
    if not failures:
        duration = max(0.0, (ended_at - incidents[0].occurred_at).total_seconds())
        return duration, duration
    boundaries = [incidents[0].occurred_at, *failures, ended_at]
    gaps = [
        max(0.0, (right - left).total_seconds()) for left, right in pairwise(boundaries)
    ]
    return max(0.0, (ended_at - failures[-1]).total_seconds()), max(gaps)


def _period_delta(period: IncidentPeriod) -> timedelta:
    return {
        IncidentPeriod.HOURS_24: timedelta(hours=24),
        IncidentPeriod.DAYS_7: timedelta(days=7),
        IncidentPeriod.DAYS_14: timedelta(days=14),
    }[period]


def _bounded(value: str, length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Incident text fields must not be empty.")
    return normalized[:length]

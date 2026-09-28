# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Operator-only structured incident statistics."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.operations.incidents import (
    AssociatedUserCount,
    Incident,
    IncidentErrorCount,
    IncidentOperationCount,
    IncidentPeriod,
    IncidentReport,
    IncidentSeverity,
    IncidentTotals,
)
from nahoermaar.users.domain import UserId

from .errors import error_responses
from .middleware import authenticated
from .pagination import NumberedPageView


class IncidentTotalsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warnings: int
    errors: int
    critical: int
    user_triggered: int
    system_triggered: int
    rejected: int
    failed: int
    retries: int
    recoveries: int


class IncidentErrorCountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    error_code: str
    count: int


class IncidentOperationCountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_type: str
    warnings: int
    errors: int
    count: int


class AssociatedUserCountView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    display_name: str | None
    discord_username: str | None
    rejected_commands: int


class IncidentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    occurred_at: datetime
    severity: str
    kind: str
    component: str
    error_code: str
    actor_id: UUID | None
    operation_type: str
    correlation_id: UUID
    trigger: str


class IncidentReportView(NumberedPageView[IncidentView]):
    period: IncidentPeriod
    started_at: datetime
    ended_at: datetime
    retention_days: int
    recorded_since: datetime | None
    totals: IncidentTotalsView
    common_errors: tuple[IncidentErrorCountView, ...]
    operations: tuple[IncidentOperationCountView, ...]
    associated_users: tuple[AssociatedUserCountView, ...]
    current_failure_free_seconds: float | None
    longest_failure_free_seconds: float | None


def router(application: Application) -> APIRouter:
    """Build the admin-only incident report endpoint."""
    routes = APIRouter(prefix="/api/incidents", tags=["incidents"])

    @routes.get(
        "",
        operation_id="getIncidentReport",
        responses=error_responses(401, 403, 422, 500, 503),
    )
    async def incident_report(
        request: Request,
        period: Annotated[IncidentPeriod, Query()] = IncidentPeriod.HOURS_24,
        severity: IncidentSeverity | None = None,
        component: Annotated[str | None, Query(max_length=80)] = None,
        code: Annotated[str | None, Query(max_length=120)] = None,
        actor_id: UUID | None = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> IncidentReportView:
        await application.users.access.require_admin(authenticated(request).user.id)
        return _report_view(
            await application.operations.incidents.report(
                period,
                page=page,
                page_size=page_size,
                severity=severity,
                component=component,
                error_code=code,
                actor_id=UserId(actor_id) if actor_id is not None else None,
            )
        )

    return routes


def _report_view(report: IncidentReport) -> IncidentReportView:
    return IncidentReportView(
        period=report.period,
        started_at=report.started_at,
        ended_at=report.ended_at,
        retention_days=report.retention_days,
        recorded_since=report.recorded_since,
        totals=_totals_view(report.totals),
        common_errors=tuple(_error_view(item) for item in report.common_errors),
        operations=tuple(_operation_view(item) for item in report.operations),
        associated_users=tuple(_user_view(item) for item in report.associated_users),
        current_failure_free_seconds=report.current_failure_free_seconds,
        longest_failure_free_seconds=report.longest_failure_free_seconds,
        items=tuple(_incident_view(item) for item in report.items),
        page=report.page,
        page_size=report.page_size,
        total=report.total,
        page_count=report.page_count,
        snapshot=None,
    )


def _totals_view(totals: IncidentTotals) -> IncidentTotalsView:
    return IncidentTotalsView(
        warnings=totals.warnings,
        errors=totals.errors,
        critical=totals.critical,
        user_triggered=totals.user_triggered,
        system_triggered=totals.system_triggered,
        rejected=totals.rejected,
        failed=totals.failed,
        retries=totals.retries,
        recoveries=totals.recoveries,
    )


def _error_view(item: IncidentErrorCount) -> IncidentErrorCountView:
    return IncidentErrorCountView(
        component=item.component,
        error_code=item.error_code,
        count=item.count,
    )


def _operation_view(item: IncidentOperationCount) -> IncidentOperationCountView:
    return IncidentOperationCountView(
        operation_type=item.operation_type,
        warnings=item.warnings,
        errors=item.errors,
        count=item.count,
    )


def _user_view(item: AssociatedUserCount) -> AssociatedUserCountView:
    return AssociatedUserCountView(
        user_id=item.identity.user_id,
        display_name=item.identity.display_name,
        discord_username=item.identity.discord_username,
        rejected_commands=item.rejected_commands,
    )


def _incident_view(item: Incident) -> IncidentView:
    return IncidentView(
        id=item.id,
        occurred_at=item.occurred_at,
        severity=item.severity.value,
        kind=item.kind.value,
        component=item.component,
        error_code=item.error_code,
        actor_id=item.actor_id,
        operation_type=item.operation_type,
        correlation_id=item.correlation_id,
        trigger=item.trigger.value,
    )

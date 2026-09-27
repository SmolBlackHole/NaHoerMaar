# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Admin controls and persisted history for bounded application jobs."""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.service import CatalogMaintenanceStatus
from nahoermaar.operations.housekeeping import HousekeepingStatus
from nahoermaar.operations.jobs import HISTORY_RETENTION_DAYS, JobRun, JobRunDetail

from .middleware import authenticated

_LOGGER = logging.getLogger(__name__)


class BackgroundJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    running: bool
    interval_seconds: float
    default_batch_size: int
    max_batch_size: int
    parallel_requests: int
    active_batch_size: int | None
    active_trigger: str | None
    active_candidates: int
    active_processed: int
    last_trigger: str | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    last_candidates: int
    last_processed: int
    last_changed: int
    last_failures: int
    last_error: str | None


class BackgroundJobRunDetailView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    outcome: str
    label: str
    summary: str
    affected_count: int
    subject_id: str | None
    source: str | None
    error_code: str | None


class BackgroundJobRunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_id: str
    trigger: str
    status: str
    requested_by: UUID | None
    requested_count: int
    candidate_count: int
    processed_count: int
    changed_count: int
    failure_count: int
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    error_code: str | None
    details: tuple[BackgroundJobRunDetailView, ...]


class BackgroundJobsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: tuple[BackgroundJobView, ...]
    recent_runs: tuple[BackgroundJobRunView, ...]
    history_retention_days: int


CatalogBatchSize = Annotated[int, Field(ge=1, le=100)]
HousekeepingBatchSize = Annotated[int, Field(ge=1, le=10_000)]


class RunCatalogMaintenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: CatalogBatchSize = 10


class RunHousekeeping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: HousekeepingBatchSize = 10_000


def router(application: Application) -> APIRouter:
    """Build admin-only status and trigger endpoints for background work."""
    routes = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @routes.get("")
    async def background_jobs(request: Request) -> BackgroundJobsView:
        await application.access.require_admin(authenticated(request).user.id)
        runs = await application.jobs.recent()
        return BackgroundJobsView(
            jobs=(
                _catalog_maintenance_view(application.catalog.maintenance_status()),
                _housekeeping_view(application.housekeeping.status()),
            ),
            recent_runs=tuple(_run_view(run) for run in runs),
            history_retention_days=HISTORY_RETENTION_DAYS,
        )

    @routes.post(
        "/catalog-maintenance",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_catalog_maintenance(
        body: RunCatalogMaintenance,
        request: Request,
    ) -> BackgroundJobView:
        actor = authenticated(request).user
        await application.access.require_admin(actor.id)
        current = application.catalog.trigger_maintenance(
            batch_size=body.batch_size,
            actor_id=actor.id,
        )
        _LOGGER.info(
            "jobs.catalog_maintenance_requested actor_id=%s batch=%d",
            actor.id,
            body.batch_size,
        )
        return _catalog_maintenance_view(current)

    @routes.post(
        "/housekeeping",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_housekeeping(
        body: RunHousekeeping,
        request: Request,
    ) -> BackgroundJobView:
        actor = authenticated(request).user
        await application.access.require_admin(actor.id)
        current = application.housekeeping.trigger(body.batch_size, actor.id)
        _LOGGER.info(
            "jobs.housekeeping_requested actor_id=%s batch=%d",
            actor.id,
            body.batch_size,
        )
        return _housekeeping_view(current)

    return routes


def _catalog_maintenance_view(
    current: CatalogMaintenanceStatus,
) -> BackgroundJobView:
    return BackgroundJobView(
        id="catalog-maintenance",
        label="Catalog maintenance",
        description=(
            "Repairs incomplete track metadata and refreshes stale search results."
        ),
        running=current.running,
        interval_seconds=current.interval_seconds,
        default_batch_size=current.default_batch_size,
        max_batch_size=100,
        parallel_requests=current.parallel_requests,
        active_batch_size=current.active_batch_size,
        active_trigger=current.active_trigger,
        active_candidates=current.active_candidates,
        active_processed=current.active_processed,
        last_trigger=current.last_trigger,
        last_started_at=current.last_started_at,
        last_finished_at=current.last_finished_at,
        next_run_at=current.next_run_at,
        last_candidates=(
            current.last_metadata_candidates + current.last_discovery_candidates
        ),
        last_processed=(
            current.last_metadata_repaired
            + current.last_discovery_refreshed
            + current.last_failures
        ),
        last_changed=(
            current.last_metadata_repaired + current.last_discovery_refreshed
        ),
        last_failures=current.last_failures,
        last_error=current.last_error,
    )


def _housekeeping_view(current: HousekeepingStatus) -> BackgroundJobView:
    return BackgroundJobView(
        id="housekeeping",
        label="Data housekeeping",
        description=(
            "Removes expired sessions, receipts, cache snapshots, incidents and "
            "superseded avatars."
        ),
        running=current.running,
        interval_seconds=current.interval_seconds,
        default_batch_size=current.default_batch_size,
        max_batch_size=10_000,
        parallel_requests=1,
        active_batch_size=current.active_batch_size,
        active_trigger=current.active_trigger,
        active_candidates=current.active_candidates,
        active_processed=current.active_processed,
        last_trigger=current.last_trigger,
        last_started_at=current.last_started_at,
        last_finished_at=current.last_finished_at,
        next_run_at=current.next_run_at,
        last_candidates=current.last_candidates,
        last_processed=current.last_processed,
        last_changed=current.last_changed,
        last_failures=current.last_failures,
        last_error=current.last_error,
    )


def _run_view(run: JobRun) -> BackgroundJobRunView:
    return BackgroundJobRunView(
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
        duration_seconds=run.duration_seconds,
        error_code=run.error_code,
        details=tuple(_run_detail_view(detail) for detail in run.details),
    )


def _run_detail_view(detail: JobRunDetail) -> BackgroundJobRunDetailView:
    return BackgroundJobRunDetailView(
        kind=detail.kind,
        outcome=detail.outcome,
        label=detail.label,
        summary=detail.summary,
        affected_count=detail.affected_count,
        subject_id=detail.subject_id,
        source=detail.source,
        error_code=detail.error_code,
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Admin controls and persisted history for bounded application jobs."""

from datetime import datetime
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.operations.jobs import (
    HISTORY_RETENTION_DAYS,
    JobHealth,
    JobId,
    JobRun,
    JobRunCursor,
    JobRunDetail,
    JobRunStatus,
)
from nahoermaar.operations.scheduler import (
    JobCoordinatorError,
    JobCoordinatorErrorCode,
    JobRunRequest,
    JobStatus,
)

from .errors import ApiError, ApiErrorCode
from .middleware import authenticated
from .pagination import TimestampPosition, decode_position, encode_position

_LOGGER = logging.getLogger(__name__)


class IntegerJobControlView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: int
    minimum: int
    maximum: int


class BooleanJobControlView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: bool


class BackgroundJobControlsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: IntegerJobControlView
    preview: BooleanJobControlView | None
    age_days: IntegerJobControlView | None


class ActiveJobOptionsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: int
    preview: bool | None
    age_days: int | None


class BackgroundJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    module: str
    label: str
    description: str
    scope: tuple[str, ...]
    health: JobHealth
    running: bool
    interval_seconds: float
    controls: BackgroundJobControlsView
    parallel_requests: int
    active_options: ActiveJobOptionsView | None
    active_trigger: str | None
    active_candidates: int
    active_processed: int
    progress_unit: str | None
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


class BackgroundJobRunSummaryView(BaseModel):
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


class BackgroundJobRunPageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: tuple[BackgroundJobRunSummaryView, ...]
    next_cursor: str | None


class BackgroundJobsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: tuple[BackgroundJobView, ...]
    history_retention_days: int


JobBatchSize = Annotated[int, Field(ge=1, le=10_000)]


class RunJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: JobBatchSize | None = None
    preview: bool | None = None
    age_days: Annotated[int, Field(ge=1, le=3650)] | None = None


def router(application: Application) -> APIRouter:
    """Build admin-only status and trigger endpoints for background work."""
    routes = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @routes.get("")
    async def background_jobs(request: Request) -> BackgroundJobsView:
        await application.users.access.require_admin(authenticated(request).user.id)
        jobs = await application.operations.jobs.statuses()
        return BackgroundJobsView(
            jobs=tuple(_coordinated_job_view(current) for current in jobs),
            history_retention_days=HISTORY_RETENTION_DAYS,
        )

    @routes.get("/runs")
    async def background_job_runs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        job_id: JobId | None = None,
        run_status: Annotated[JobRunStatus | None, Query(alias="status")] = None,
    ) -> BackgroundJobRunPageView:
        await application.users.access.require_admin(authenticated(request).user.id)
        page = await application.operations.job_runs.runs(
            limit=limit,
            cursor=_decode_cursor(cursor),
            job_id=job_id,
            status=run_status,
        )
        return BackgroundJobRunPageView(
            entries=tuple(_run_summary_view(run) for run in page.entries),
            next_cursor=_encode_cursor(page.next_cursor),
        )

    @routes.get("/runs/{run_id}")
    async def background_job_run(
        run_id: UUID,
        request: Request,
    ) -> BackgroundJobRunView:
        await application.users.access.require_admin(authenticated(request).user.id)
        run = await application.operations.job_runs.run(run_id)
        if run is None:
            raise ApiError(ApiErrorCode.NOT_FOUND, status.HTTP_404_NOT_FOUND)
        return _run_view(run)

    @routes.post(
        "/{job_id}/runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_job(
        job_id: str,
        body: RunJob,
        request: Request,
    ) -> BackgroundJobView:
        actor = authenticated(request).user
        await application.users.access.require_admin(actor.id)
        try:
            current = await application.operations.jobs.trigger(
                job_id,
                JobRunRequest(
                    batch_size=body.batch_size,
                    preview=body.preview,
                    age_days=body.age_days,
                ),
                actor.id,
            )
        except JobCoordinatorError as error:
            raise _job_error(error) from error
        _LOGGER.info(
            "jobs.run_requested job=%s actor_id=%s batch=%s preview=%s age_days=%s",
            job_id,
            actor.id,
            body.batch_size,
            body.preview,
            body.age_days,
        )
        return _coordinated_job_view(current)

    return routes


def _coordinated_job_view(current: JobStatus) -> BackgroundJobView:
    descriptor = current.descriptor
    progress = current.progress
    return BackgroundJobView(
        id=descriptor.id,
        module=descriptor.module,
        label=descriptor.label,
        description=descriptor.description,
        scope=descriptor.scope,
        health=current.health,
        running=current.running,
        interval_seconds=descriptor.interval.total_seconds(),
        controls=BackgroundJobControlsView(
            batch_size=IntegerJobControlView(
                default=descriptor.controls.batch_size.default,
                minimum=descriptor.controls.batch_size.minimum,
                maximum=descriptor.controls.batch_size.maximum,
            ),
            preview=(
                BooleanJobControlView(default=descriptor.controls.preview.default)
                if descriptor.controls.preview is not None
                else None
            ),
            age_days=(
                IntegerJobControlView(
                    default=descriptor.controls.age_days.default,
                    minimum=descriptor.controls.age_days.minimum,
                    maximum=descriptor.controls.age_days.maximum,
                )
                if descriptor.controls.age_days is not None
                else None
            ),
        ),
        parallel_requests=descriptor.parallel_requests,
        active_options=(
            ActiveJobOptionsView(
                batch_size=current.active_options.batch_size,
                preview=current.active_options.preview,
                age_days=current.active_options.age_days,
            )
            if current.active_options is not None
            else None
        ),
        active_trigger=current.active_trigger,
        active_candidates=progress.total if progress is not None else 0,
        active_processed=progress.current if progress is not None else 0,
        progress_unit=progress.unit if progress is not None else None,
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


def _job_error(error: JobCoordinatorError) -> ApiError:
    if error.code is JobCoordinatorErrorCode.UNKNOWN_JOB:
        return ApiError(ApiErrorCode.NOT_FOUND, status.HTTP_404_NOT_FOUND)
    if error.code is JobCoordinatorErrorCode.ALREADY_RUNNING:
        return ApiError(ApiErrorCode.CONFLICT, status.HTTP_409_CONFLICT)
    if error.code is JobCoordinatorErrorCode.INVALID_OPTIONS:
        return ApiError(
            ApiErrorCode.VALIDATION_FAILED,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return ApiError(
        ApiErrorCode.SERVICE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE
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


def _run_summary_view(run: JobRun) -> BackgroundJobRunSummaryView:
    return BackgroundJobRunSummaryView(
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


def _encode_cursor(cursor: JobRunCursor | None) -> str | None:
    return encode_position(
        TimestampPosition(cursor.started_at, cursor.run_id)
        if cursor is not None
        else None
    )


def _decode_cursor(value: str | None) -> JobRunCursor | None:
    position = decode_position(value)
    if position is None:
        return None
    return JobRunCursor(position.occurred_at, position.identifier)

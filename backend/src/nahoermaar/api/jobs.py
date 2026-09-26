# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Admin controls for bounded application-owned background work."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from nahoermaar.bootstrap import Application
from nahoermaar.catalog.service import CatalogMaintenanceStatus

from .middleware import authenticated

_LOGGER = logging.getLogger(__name__)


class BackgroundJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    running: bool
    interval_seconds: float
    default_batch_size: int
    parallel_requests: int
    active_batch_size: int | None
    active_trigger: str | None
    active_candidates: int
    active_processed: int
    last_trigger: str | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    next_run_at: datetime | None
    last_metadata_candidates: int
    last_discovery_candidates: int
    last_metadata_repaired: int
    last_discovery_refreshed: int
    last_error: str | None


class BackgroundJobsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: tuple[BackgroundJobView, ...]


class RunCatalogMaintenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=10, ge=1, le=100)


def router(application: Application) -> APIRouter:
    """Build admin-only status and trigger endpoints for background work."""
    routes = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @routes.get("")
    async def background_jobs(request: Request) -> BackgroundJobsView:
        await application.access.require_admin(authenticated(request).user.id)
        return BackgroundJobsView(
            jobs=(_catalog_maintenance_view(application.catalog.maintenance_status()),)
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
        current = application.catalog.trigger_maintenance(batch_size=body.batch_size)
        _LOGGER.info(
            "jobs.catalog_maintenance_requested actor_id=%s batch=%d",
            actor.id,
            body.batch_size,
        )
        return _catalog_maintenance_view(current)

    return routes


def _catalog_maintenance_view(
    current: CatalogMaintenanceStatus,
) -> BackgroundJobView:
    return BackgroundJobView(
        id="catalog-maintenance",
        label="Catalog maintenance",
        running=current.running,
        interval_seconds=current.interval_seconds,
        default_batch_size=current.default_batch_size,
        parallel_requests=current.parallel_requests,
        active_batch_size=current.active_batch_size,
        active_trigger=current.active_trigger,
        active_candidates=current.active_candidates,
        active_processed=current.active_processed,
        last_trigger=current.last_trigger,
        last_started_at=current.last_started_at,
        last_finished_at=current.last_finished_at,
        next_run_at=current.next_run_at,
        last_metadata_candidates=current.last_metadata_candidates,
        last_discovery_candidates=current.last_discovery_candidates,
        last_metadata_repaired=current.last_metadata_repaired,
        last_discovery_refreshed=current.last_discovery_refreshed,
        last_error=current.last_error,
    )

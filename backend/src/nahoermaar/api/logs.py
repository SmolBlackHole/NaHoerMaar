# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated operator access to recent process logs."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.operations.logs import LogEntry

from .middleware import authenticated


class LogEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    timestamp: datetime
    level: str
    source: str
    message: str
    request_id: str | None
    message_id: UUID | None
    correlation_id: UUID | None
    actor_id: UUID | None


class LogsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: tuple[LogEntryView, ...]
    cursor: int


def router(application: Application) -> APIRouter:
    """Build the admin-only process log endpoint."""
    routes = APIRouter(prefix="/api/logs", tags=["logs"])

    @routes.get("")
    async def recent_logs(
        request: Request,
        after: int | None = Query(default=None, ge=0),
        limit: int = Query(default=200, ge=1, le=200),
    ) -> LogsView:
        await application.access.require_admin(authenticated(request).user.id)
        entries = application.logs.entries(after=after, limit=limit)
        return LogsView(
            entries=tuple(_entry_view(entry) for entry in entries),
            cursor=entries[-1].id if entries else (after or 0),
        )

    return routes


def _entry_view(entry: LogEntry) -> LogEntryView:
    return LogEntryView(
        id=entry.id,
        timestamp=entry.timestamp,
        level=entry.level,
        source=entry.source,
        message=entry.message,
        request_id=entry.request_id,
        message_id=entry.message_id,
        correlation_id=entry.correlation_id,
        actor_id=entry.actor_id,
    )

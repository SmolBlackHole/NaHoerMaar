# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated live player snapshots over server-sent events."""

import asyncio
from collections.abc import AsyncGenerator
import logging
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from nahoermaar.bootstrap import Application
from nahoermaar.player.events import Reauthenticate
from nahoermaar.users.domain import AuthError
from nahoermaar.users.service import SESSION_COOKIE

from .middleware import authenticated
from .player import OutcomeView, PlayerView, View, player_view

_AUTH_CHECK_SECONDS = 30
_LOGGER = logging.getLogger(__name__)


class ChangeView(View):
    message_id: UUID
    correlation_id: UUID
    causation_id: UUID | None
    operation_id: UUID
    action: str
    outcome: OutcomeView
    state: PlayerView


class AuthEventView(View):
    error: str


def router(application: Application) -> APIRouter:
    routes = APIRouter(prefix="/api/events", tags=["events"])

    @routes.get(
        "",
        response_class=EventSourceResponse,
        response_model=PlayerView | ChangeView | AuthEventView,
        openapi_extra={
            "x-sse-payloads": {
                "state": {"$ref": "#/components/schemas/PlayerView"},
                "change": {"$ref": "#/components/schemas/ChangeView"},
                "auth": {"$ref": "#/components/schemas/AuthEventView"},
            }
        },
    )
    async def events(request: Request) -> AsyncGenerator[ServerSentEvent]:
        actor = authenticated(request)
        _LOGGER.info("sse.connected user_id=%s", actor.user.id)
        try:
            async for event in event_stream(
                application,
                request.cookies.get(SESSION_COOKIE),
            ):
                yield event
        finally:
            _LOGGER.info("sse.disconnected user_id=%s", actor.user.id)

    return routes


async def event_stream(
    application: Application,
    token: str | None,
) -> AsyncGenerator[ServerSentEvent]:
    """Send one resync snapshot, then committed changes without replay."""
    async with application.player.events.subscribe() as changes:
        initial = application.player.state
        last_revision = -1
        while True:
            try:
                await application.auth.authenticate(token)
                if last_revision < 0:
                    document = await player_view(application, initial)
                    await application.auth.authenticate(token)
                    last_revision = initial.session.revision
                    _LOGGER.debug(
                        "sse.state_sent session=%s revision=%d",
                        initial.session.id,
                        last_revision,
                    )
                    yield ServerSentEvent(
                        event="state",
                        id=str(last_revision),
                        data=document,
                    )
                    continue

                try:
                    async with asyncio.timeout(_AUTH_CHECK_SECONDS):
                        update = await changes.get()
                except TimeoutError:
                    continue

                if update is None:
                    _LOGGER.debug("sse.upstream_closed")
                    return
                if isinstance(update, Reauthenticate):
                    _LOGGER.debug("sse.reauthentication_requested")
                    continue
                if update.event.revision <= last_revision:
                    _LOGGER.debug(
                        "sse.stale_change_ignored revision=%d last_revision=%d",
                        update.event.revision,
                        last_revision,
                    )
                    continue

                document = await player_view(application, update.state)
                await application.auth.authenticate(token)
                last_revision = update.event.revision
                _LOGGER.debug(
                    "sse.change_sent session=%s revision=%d action=%s",
                    update.event.session_id,
                    last_revision,
                    update.event.outcome.action.value,
                )
                yield ServerSentEvent(
                    event="change",
                    id=str(last_revision),
                    data=ChangeView(
                        message_id=update.context.message_id,
                        correlation_id=update.context.correlation_id,
                        causation_id=update.context.causation_id,
                        operation_id=update.event.operation_id,
                        action=update.event.outcome.action.value,
                        outcome=OutcomeView(
                            action=update.event.outcome.action.value,
                            added_count=update.event.outcome.added_count,
                            removed_count=update.event.outcome.removed_count,
                            restored_count=update.event.outcome.restored_count,
                            skipped_count=update.event.outcome.skipped_count,
                            entry_ids=update.event.outcome.entry_ids,
                            undo_id=update.event.outcome.undo_id,
                            undo_expires_at=update.event.outcome.undo_expires_at,
                        ),
                        state=document,
                    ),
                )
            except AuthError as error:
                _LOGGER.info(
                    "sse.authentication_failed error_code=%s", error.code.value
                )
                yield ServerSentEvent(
                    event="auth",
                    data=AuthEventView(error=error.code.value),
                )
                return

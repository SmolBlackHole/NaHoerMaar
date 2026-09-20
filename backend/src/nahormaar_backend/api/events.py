# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authorized SSE delivery of committed player snapshots."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ..application.auth import ACCESS_CHECK_SECONDS, SESSION_COOKIE
from ..application.playback import PlaybackController
from ..domain.identity import AuthError
from . import schemas as dto
from .dependencies import ApiServices


def events_router(services: ApiServices) -> APIRouter:
    router = APIRouter()

    async def subscription(
        request: Request,
        active: Annotated[PlaybackController, Depends(services.player)],
    ) -> AsyncGenerator[AsyncIterator[ServerSentEvent]]:
        async with active.subscribe() as queue:

            async def snapshots() -> AsyncIterator[ServerSentEvent]:
                while True:
                    try:
                        await services.auth().authenticate(
                            request.cookies.get(SESSION_COOKIE)
                        )
                    except AuthError as exc:
                        yield ServerSentEvent(event="auth", data={"code": exc.code})
                        return
                    try:
                        async with asyncio.timeout(ACCESS_CHECK_SECONDS):
                            snapshot = await queue.get()
                    except TimeoutError:
                        continue
                    if snapshot is None:
                        return
                    # A queued update must still be authorized when it is sent.
                    try:
                        await services.auth().authenticate(
                            request.cookies.get(SESSION_COOKIE)
                        )
                    except AuthError as exc:
                        yield ServerSentEvent(event="auth", data={"code": exc.code})
                        return
                    yield ServerSentEvent(
                        event="state",
                        id=str(snapshot.revision),
                        data=dto.State.from_status(snapshot),
                    )

            yield snapshots()

    @router.get("/api/events", response_class=EventSourceResponse)
    async def events(
        snapshots: Annotated[AsyncIterator[ServerSentEvent], Depends(subscription)],
    ) -> AsyncIterator[ServerSentEvent]:
        async for snapshot in snapshots:
            yield snapshot

    return router

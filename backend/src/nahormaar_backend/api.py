# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Local HTTP controls and committed player snapshots."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api_models as dto
from . import commands
from .config import Settings
from .playback import PlaybackController
from .runtime import open_runtime
from .storage import StorageError

type RuntimeFactory = Callable[[], AbstractAsyncContextManager[PlaybackController]]
type RequestID = Annotated[UUID, Header(alias="Idempotency-Key")]


class SameOrigin:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and origin != expected:
                response = JSONResponse({"code": "origin_forbidden"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    runtime_factory: RuntimeFactory | None = None,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> FastAPI:
    controller: PlaybackController | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        nonlocal controller
        runtime = (
            runtime_factory()
            if runtime_factory is not None
            else open_runtime(Settings.from_env())
        )
        async with runtime as active:
            controller = active

            async def stop_streams() -> None:
                if shutdown_event is not None:
                    await shutdown_event.wait()
                    active.close_events()

            watcher = asyncio.create_task(stop_streams())
            try:
                yield
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                active.close_events()
                controller = None

    app = FastAPI(title="NaHörMaar", version="0.1.0", lifespan=lifespan)
    app.add_middleware(SameOrigin)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )

    async def player() -> PlaybackController:
        if controller is None:
            raise HTTPException(503, detail="Backend is not running.")
        return controller

    async def subscription(
        active: Annotated[PlaybackController, Depends(player)],
    ) -> AsyncGenerator[AsyncIterator[ServerSentEvent]]:
        async with active.subscribe() as queue:

            async def snapshots() -> AsyncIterator[ServerSentEvent]:
                while (snapshot := await queue.get()) is not None:
                    yield ServerSentEvent(
                        event="state",
                        id=str(snapshot.revision),
                        data=dto.State.from_status(snapshot),
                    )

            yield snapshots()

    async def state(
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.State:
        return dto.State.from_status(await active.read_status())

    app.get("/api/state", response_model=dto.State)(state)

    @app.exception_handler(StorageError)
    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"code": "backend_unavailable"}, status_code=503)

    async def mutate(
        request_id: UUID,
        command: commands.Command,
        response: Response,
        active: PlaybackController,
    ) -> dto.MutationResult:
        reply = await active.request(request_id, command)
        response.status_code = reply.outcome.status_code
        return dto.MutationResult(
            request_id=request_id,
            code=reply.outcome.code,
            entry_id=reply.outcome.entry_id,
            replayed=reply.replayed,
            snapshot=dto.State.from_status(reply.status),
        )

    @app.get("/api/channels")
    async def channels(
        active: Annotated[PlaybackController, Depends(player)],
    ) -> list[dto.Channel]:
        await active.read_status()
        return [
            dto.Channel(
                id=str(channel.id),
                name=channel.name,
                can_connect=channel.can_connect,
                can_speak=channel.can_speak,
            )
            for channel in active.channels()
        ]

    @app.get("/api/events", response_class=EventSourceResponse)
    async def events(
        snapshots: Annotated[AsyncIterator[ServerSentEvent], Depends(subscription)],
    ) -> AsyncIterator[ServerSentEvent]:
        async for snapshot in snapshots:
            yield snapshot

    @app.post("/api/queue")
    async def add(
        body: dto.AddInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id,
            commands.Add(
                body.source_url,
                body.added_by.to_contributor()
                if body.added_by
                else dto.ContributorData.default_contributor(),
            ),
            response,
            active,
        )

    @app.delete("/api/queue/{entry_id}")
    async def remove(
        entry_id: UUID,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(request_id, commands.Remove(entry_id), response, active)

    @app.post("/api/queue/{entry_id}/move")
    async def move(
        entry_id: UUID,
        body: dto.MoveInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id,
            commands.Move(entry_id, body.before_entry_id, body.expected_queue_revision),
            response,
            active,
        )

    @app.post("/api/queue/clear")
    async def clear(
        body: dto.QueueRevisionInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id, commands.Clear(body.expected_queue_revision), response, active
        )

    @app.post("/api/player/{action}")
    async def control(
        action: Literal["play", "pause", "skip", "stop"],
        body: dto.ControlInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id,
            commands.Control(action, body.expected_playback_id),
            response,
            active,
        )

    @app.put("/api/player/volume")
    async def volume(
        body: dto.VolumeInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(request_id, commands.Volume(body.volume), response, active)

    @app.put("/api/player/seek")
    async def seek(
        body: dto.SeekInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id,
            commands.Seek(body.position_seconds, body.expected_playback_id),
            response,
            active,
        )

    @app.put("/api/voice/channel")
    async def connect(
        body: dto.ChannelInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            request_id, commands.Connect(int(body.channel_id)), response, active
        )

    @app.delete("/api/voice/channel")
    async def disconnect(
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(request_id, commands.Disconnect(), response, active)

    return app

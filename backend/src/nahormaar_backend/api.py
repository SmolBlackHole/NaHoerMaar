# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Local HTTP controls and committed player snapshots."""

import asyncio
import json
from importlib.resources import files
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.exc import SQLAlchemyError

from . import api_models as dto
from . import commands
from .config import Settings
from .audio import TrackError
from .catalog import PlaylistPreview, MediaCatalog
from .search import CatalogBusy, SearchPage, SearchSource
from .playback import PlaybackController
from .runtime import open_runtime
from .storage import StorageError
from .auth import (
    Auth,
    AuthSettings,
    AuthError,
    IdentityProvider,
    ACCESS_CHECK_SECONDS,
    SESSION_COOKIE,
)
from .auth_api import AuthBoundary, CurrentUser, auth_router

type RuntimeFactory = Callable[[], AbstractAsyncContextManager[PlaybackController]]
type RequestID = Annotated[UUID, Header(alias="Idempotency-Key")]


def create_app(
    runtime_factory: RuntimeFactory | None = None,
    *,
    shutdown_event: asyncio.Event | None = None,
    auth_settings: AuthSettings | None = None,
    identity_provider: IdentityProvider | None = None,
) -> FastAPI:
    controller: PlaybackController | None = None
    settings = auth_settings or AuthSettings.from_env()
    authentication: Auth | None = None

    def auth() -> Auth:
        if authentication is None:
            raise AuthError("auth_unavailable", 503)
        return authentication

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        nonlocal controller, authentication
        runtime = (
            runtime_factory()
            if runtime_factory is not None
            else open_runtime(Settings.from_env())
        )
        async with runtime as active:
            controller = active
            avatars = cast(
                list[str],
                json.loads(
                    files("nahormaar_backend")
                    .joinpath("avatars.json")
                    .read_text(encoding="utf-8")
                ),
            )
            authentication = Auth(settings, tuple(avatars), provider=identity_provider)

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
                await authentication.close()
                authentication = None

    app = FastAPI(title="NaHörMaar", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthBoundary, service=auth)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1",
            "localhost",
            "[::1]",
            urlsplit(settings.public_origin).hostname or "localhost",
        ],
    )
    app.include_router(auth_router(auth))

    @app.exception_handler(SQLAlchemyError)
    async def account_storage_failed(
        request: Request, error: SQLAlchemyError
    ) -> JSONResponse:
        return JSONResponse({"code": "auth_unavailable"}, status_code=503)

    @app.exception_handler(AuthError)
    async def auth_failed(request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse({"code": error.code}, status_code=error.status)

    async def player() -> PlaybackController:
        if controller is None:
            raise HTTPException(503, detail="Backend is not running.")
        return controller

    async def catalog(
        active: Annotated[PlaybackController, Depends(player)],
    ) -> MediaCatalog:
        if active.catalog is None:
            raise HTTPException(503, detail="YouTube discovery is not running.")
        return active.catalog

    @app.exception_handler(CatalogBusy)
    async def discovery_busy(request: Request, error: CatalogBusy) -> JSONResponse:
        return JSONResponse(
            {"detail": str(error)}, status_code=429, headers={"Retry-After": "5"}
        )

    @app.exception_handler(TrackError)
    async def discovery_failed(request: Request, error: TrackError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=502)

    @app.get("/api/catalog/search")
    async def search(
        q: Annotated[str, Query(min_length=1, max_length=200)],
        library: Annotated[MediaCatalog, Depends(catalog)],
        offset: Annotated[int, Query(ge=0, le=90, multiple_of=10)] = 0,
        source: SearchSource = SearchSource.MUSIC,
        snapshot_id: Annotated[str | None, Query(min_length=32, max_length=32)] = None,
    ) -> SearchPage:
        if not q.strip():
            raise HTTPException(422, detail="Enter a title or artist.")
        try:
            return await library.search(q, offset, source, snapshot_id)
        except KeyError as exc:
            raise HTTPException(
                410, detail="These results expired. Refresh the search."
            ) from exc

    @app.post("/api/youtube/playlists", status_code=202)
    async def preview_playlist(
        user: CurrentUser,
        body: dto.PlaylistInput,
        request_id: RequestID,
        library: Annotated[MediaCatalog, Depends(catalog)],
    ) -> PlaylistPreview:
        try:
            return library.start_preview(
                body.source_url.strip(), request_id, owner_id=user.account.profile.id
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.get("/api/youtube/playlists/{preview_id}")
    async def playlist_preview(
        preview_id: UUID,
        library: Annotated[MediaCatalog, Depends(catalog)],
        user: CurrentUser,
    ) -> PlaylistPreview:
        try:
            return library.preview(preview_id, owner_id=user.account.profile.id)
        except KeyError as exc:
            raise HTTPException(
                410, detail="This playlist preview expired. Open the playlist again."
            ) from exc

    @app.delete("/api/youtube/playlists/{preview_id}")
    async def cancel_playlist(
        preview_id: UUID,
        library: Annotated[MediaCatalog, Depends(catalog)],
        user: CurrentUser,
    ) -> PlaylistPreview:
        try:
            return await library.cancel_preview(
                preview_id, owner_id=user.account.profile.id
            )
        except KeyError as exc:
            raise HTTPException(410, detail="This playlist preview expired.") from exc

    async def subscription(
        request: Request,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> AsyncGenerator[AsyncIterator[ServerSentEvent]]:
        async with active.subscribe() as queue:

            async def snapshots() -> AsyncIterator[ServerSentEvent]:
                while True:
                    try:
                        await auth().authenticate(request.cookies.get(SESSION_COOKIE))
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
                        await auth().authenticate(request.cookies.get(SESSION_COOKIE))
                    except AuthError as exc:
                        yield ServerSentEvent(event="auth", data={"code": exc.code})
                        return
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
        user: CurrentUser,
        request_id: UUID,
        command: commands.Command,
        response: Response,
        active: PlaybackController,
    ) -> dto.MutationResult:
        reply = await active.request(
            request_id, command, actor_id=user.account.profile.id
        )
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
                guild_id=str(channel.guild_id),
                guild_name=channel.guild_name,
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
        user: CurrentUser,
        body: dto.AddInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Add(
                body.source_url,
                user.account.profile,
            ),
            response,
            active,
        )

    @app.post("/api/queue/batch")
    async def add_many(
        user: CurrentUser,
        body: dto.BatchInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.AddMany(
                body.source_urls,
                user.account.profile,
            ),
            response,
            active,
        )

    @app.delete("/api/queue/{entry_id}")
    async def remove(
        user: CurrentUser,
        entry_id: UUID,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Remove(entry_id), response, active
        )

    @app.post("/api/queue/{entry_id}/move")
    async def move(
        user: CurrentUser,
        entry_id: UUID,
        body: dto.MoveInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Move(entry_id, body.before_entry_id, body.expected_queue_revision),
            response,
            active,
        )

    @app.post("/api/queue/clear")
    async def clear(
        user: CurrentUser,
        body: dto.ClearInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Clear(body.expected_queue_revision, body.contributor_id),
            response,
            active,
        )

    @app.post("/api/player/{action}")
    async def control(
        user: CurrentUser,
        action: Literal["play", "pause", "skip", "stop"],
        body: dto.ControlInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Control(action, body.expected_playback_id),
            response,
            active,
        )

    @app.put("/api/player/volume")
    async def volume(
        user: CurrentUser,
        body: dto.VolumeInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Volume(body.volume), response, active
        )

    @app.put("/api/player/seek")
    async def seek(
        user: CurrentUser,
        body: dto.SeekInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Seek(body.position_seconds, body.expected_playback_id),
            response,
            active,
        )

    @app.put("/api/voice/channel")
    async def connect(
        user: CurrentUser,
        body: dto.ChannelInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Connect(int(body.channel_id)), response, active
        )

    @app.delete("/api/voice/channel")
    async def disconnect(
        user: CurrentUser,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(player)],
    ) -> dto.MutationResult:
        return await mutate(user, request_id, commands.Disconnect(), response, active)

    return app

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Engine-native HTTP operations and committed-state event delivery."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..application.auth import ACCESS_CHECK_SECONDS, SESSION_COOKIE, Auth
from ..domain.identity import AuthError
from .domain.catalog import MediaReference, PlaylistPage, TrackFinding, TrackPage
from .catalog import Catalog
from .domain.playback import Control, Join, Seek, SetCrossfade, SetVolume
from .domain.queue import Add, Clear, Move, Outcome, Remove, Undo
from .domain.radio import RadioStrategy, RetryRadio, StartRadio, StopRadio
from .domain.sessions import SessionSnapshot
from .http_auth import AuthBoundary, CurrentUser, auth_router
from .providers import ProviderError, UnsupportedCapability
from .runtime import Services
from .session import Command, action_for
from .api_models import (
    AccessEventView,
    AccessGrantView,
    AccessView,
    ApiError,
    CatalogEntryView,
    ChangeView,
    ChannelView,
    CheckpointView,
    DiscoveryView,
    DiscordMembersView,
    DiscordMemberView,
    HistoryView,
    ManualView,
    LogsView,
    MetadataView,
    MutationView,
    OutcomeView,
    PlaybackView,
    PlaylistView,
    ProfileView,
    QueueEntryView,
    RadioView,
    SessionSettingsView,
    SessionView,
    TrackView,
)
from .logs import RecentLogs

type OperationID = Annotated[UUID, Header(alias="Idempotency-Key")]
_LOGGER = logging.getLogger(__name__)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AddInput(Input):
    track_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    skip_duplicates: bool = False


class ClearInput(Input):
    expected_queue_revision: int = Field(ge=0)
    contributor_id: UUID | None = None


class MoveInput(Input):
    expected_queue_revision: int = Field(ge=0)
    before_entry_id: UUID | None


class ControlInput(Input):
    action: Literal["play", "pause", "skip", "stop", "leave"]
    expected_attempt_id: UUID | None = None


class SeekInput(Input):
    seconds: float = Field(ge=0)
    expected_attempt_id: UUID


class VolumeInput(Input):
    volume: float = Field(ge=0, le=1)


class CrossfadeInput(Input):
    seconds: Literal[0, 3, 4, 5, 6, 7]


class JoinInput(Input):
    channel_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class LinkInput(Input):
    source_url: str = Field(min_length=1, max_length=2048)
    provider: str | None = Field(default=None, max_length=40)


class PlaylistInput(LinkInput):
    refresh: bool = False


class RadioInput(Input):
    seed: MediaReference
    expected_generation: UUID | None


async def state_document(
    services: Services, snapshot: SessionSnapshot, outcome: Outcome | None = None
) -> SessionView:
    identifiers = {entry.track_id for entry in snapshot.queue.entries} | {
        record.track_id for record in snapshot.history
    }
    if snapshot.checkpoint.track_id:
        identifiers.add(snapshot.checkpoint.track_id)
    if outcome:
        identifiers.update(entry.track_id for entry in outcome.entries)
    tracks = await services.metadata.get_many(tuple(identifiers))
    radio = snapshot.strategy
    if isinstance(radio, RadioStrategy) and radio.seed.kind == "track":
        seed = await services.metadata.get_many((radio.seed.identity,))
        tracks = (*tracks, *seed)
    playback = snapshot.playback
    return SessionView(
        session=SessionSettingsView.model_validate(
            asdict(snapshot.settings)
            | {
                "channel_id": str(snapshot.settings.channel_id)
                if snapshot.settings.channel_id
                else None,
            }
        ),
        queue=tuple(
            QueueEntryView.model_validate(entry) for entry in snapshot.queue.entries
        ),
        checkpoint=CheckpointView.model_validate(snapshot.checkpoint),
        playback=PlaybackView(
            phase=playback.phase,
            attempt_id=playback.attempt_id,
            connection="connecting"
            if playback.joining_id
            else "connected"
            if playback.connection_id
            else "disconnected",
            duration_seconds=playback.duration_seconds,
            error=playback.error,
        ),
        history=tuple(
            HistoryView.model_validate(record) for record in snapshot.history
        ),
        radio=RadioView.model_validate(radio)
        if isinstance(radio, RadioStrategy)
        else ManualView(),
        tracks={str(track.id): TrackView.model_validate(track) for track in tracks},
    )


async def event_stream(
    services: Services, token: str | None
) -> AsyncGenerator[ServerSentEvent]:
    """Always send a resync snapshot; the in-memory bus is not a durable log."""
    async with services.session.events.subscribe() as changes:
        initial: SessionSnapshot | None = services.session.snapshot
        while True:
            try:
                await services.auth.authenticate(token)
                if initial is not None:
                    document = await state_document(services, initial)
                    await services.auth.authenticate(token)
                    yield ServerSentEvent(
                        event="state", id=str(initial.settings.revision), data=document
                    )
                    initial = None
                    continue
                async with asyncio.timeout(ACCESS_CHECK_SECONDS):
                    change = await changes.get()
                if change is None:
                    return
                document = await state_document(services, change.after, change.outcome)
                await services.auth.authenticate(token)
                yield ServerSentEvent(
                    event="change",
                    id=str(change.after.settings.revision),
                    data=ChangeView(
                        request_id=change.request_id,
                        action=change.action,
                        outcome=OutcomeView.model_validate(change.outcome),
                        state=document,
                    ),
                )
            except TimeoutError:
                continue
            except AuthError as error:
                yield ServerSentEvent(event="auth", data=ApiError(code=error.code))
                return
            except SQLAlchemyError:
                yield ServerSentEvent(
                    event="auth", data=ApiError(code="auth_unavailable")
                )
                return


def create_app(
    runtime: Callable[[], AbstractAsyncContextManager[Services]],
    *,
    public_origin: str,
    shutdown_event: asyncio.Event | None = None,
) -> FastAPI:
    active: Services | None = None
    recent_logs = RecentLogs()

    def services() -> Services:
        if active is None:
            raise AuthError("backend_unavailable", 503)
        return active

    def auth() -> Auth:
        return services().auth

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        nonlocal active
        bot_logger = logging.getLogger("nahormaar_backend")
        bot_logger.addHandler(recent_logs)
        try:
            async with runtime() as opened:
                if opened.auth.settings.public_origin != public_origin:
                    raise ValueError("HTTP and authentication origins must agree.")
                active = opened

                async def stop_streams() -> None:
                    if shutdown_event is not None:
                        await shutdown_event.wait()
                        opened.session.events.close()

                watcher = asyncio.create_task(
                    stop_streams(), name="engine-http-shutdown"
                )
                try:
                    yield
                finally:
                    if shutdown_event is not None:
                        shutdown_event.set()
                    opened.session.events.close()
                    active = None
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
        finally:
            bot_logger.removeHandler(recent_logs)

    error_responses: dict[int | str, dict[str, object]] = {
        status: {"model": ApiError, "description": description}
        for status, description in (
            (401, "Unauthorized"),
            (403, "Forbidden"),
            (404, "Not Found"),
            (409, "Conflict"),
            (422, "Unprocessable Content"),
            (502, "Bad Gateway"),
            (503, "Service Unavailable"),
        )
    }
    app = FastAPI(
        title="NaHörMaar engine",
        lifespan=lifespan,
        responses=error_responses,
    )
    app.add_middleware(AuthBoundary, service=auth)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            urlsplit(public_origin).hostname or "localhost",
            "backend",
            "localhost",
            "127.0.0.1",
            "[::1]",
        ],
    )
    app.include_router(auth_router(auth))

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        if active is None:
            return JSONResponse({"status": "starting"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse(
            ApiError(code=error.code).model_dump(mode="json"), status_code=error.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            ApiError(code="invalid_request").model_dump(mode="json"), status_code=422
        )

    @app.exception_handler(ValueError)
    @app.exception_handler(LookupError)
    async def invalid(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            ApiError(
                code="not_found"
                if isinstance(error, LookupError)
                else "invalid_request"
            ).model_dump(mode="json"),
            status_code=404 if isinstance(error, LookupError) else 422,
        )

    @app.exception_handler(RuntimeError)
    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        if isinstance(error, UnsupportedCapability):
            return JSONResponse(
                ApiError(code="unsupported_source", message=str(error)).model_dump(
                    mode="json"
                ),
                status_code=422,
            )
        if isinstance(error, ProviderError):
            return JSONResponse(
                ApiError(
                    code="provider_unavailable", retryable=error.retryable
                ).model_dump(mode="json"),
                status_code=502,
            )
        _LOGGER.error("engine.api.failed: %s", type(error).__name__)
        return JSONResponse(
            ApiError(code="backend_unavailable", retryable=True).model_dump(
                mode="json"
            ),
            status_code=503,
        )

    async def mutate(
        command: Command,
        operation: UUID,
        user: CurrentUser,
        *,
        attempt: UUID | None = None,
    ) -> JSONResponse:
        value = services()
        reply = await value.session.request(
            operation,
            command,
            actor=user.account.profile,
            expected_attempt_id=attempt,
        )
        code = reply.outcome.code
        status = (
            200
            if code == "ok"
            else 409
            if code.endswith("conflict") or code == "undo_unavailable"
            else 404
            if code == "entry_not_found"
            else 422
        )
        return JSONResponse(
            MutationView(
                request_id=operation,
                action=action_for(command),
                state=await state_document(value, reply.snapshot, reply.outcome),
                outcome=OutcomeView.model_validate(reply.outcome),
                replayed=reply.replayed,
            ).model_dump(mode="json"),
            status_code=status,
        )

    @app.get("/api/session")
    async def state() -> SessionView:
        return await state_document(services(), services().session.snapshot)

    @app.get("/api/diagnostics/logs")
    async def logs(
        user: CurrentUser, after: Annotated[int | None, Query(ge=0)] = None
    ) -> LogsView:
        if not user.admin:
            raise AuthError("access_denied", 403)
        return LogsView(entries=recent_logs.since(after))

    async def access_document(value: Services) -> AccessView:
        operators = value.access.operators
        return AccessView(
            owner_id=operators.owner_id,
            admin_ids=operators.admin_ids,
            grants=tuple(
                AccessGrantView.model_validate(grant)
                for grant in await value.access.grants()
            ),
            history=tuple(
                AccessEventView.model_validate(event)
                for event in await value.access.history()
            ),
        )

    @app.get("/api/admin/access")
    async def access_state(user: CurrentUser) -> AccessView:
        await services().access.require_admin(user.account.discord_id)
        return await access_document(services())

    @app.put("/api/admin/access/{discord_id}")
    async def grant_access(discord_id: str, user: CurrentUser) -> AccessView:
        value = services()
        await value.access.grant(user.account.discord_id, discord_id)
        return await access_document(value)

    @app.delete("/api/admin/access/{discord_id}")
    async def revoke_access(discord_id: str, user: CurrentUser) -> AccessView:
        value = services()
        await value.access.revoke(user.account.discord_id, discord_id)
        return await access_document(value)

    @app.get("/api/admin/access/members")
    async def discord_members(user: CurrentUser) -> DiscordMembersView:
        await services().access.require_admin(user.account.discord_id)
        return DiscordMembersView(
            members=tuple(
                DiscordMemberView.model_validate(member)
                for member in services().directory.members()
            )
        )

    @app.get("/api/profiles/{discord_id}")
    async def profile(discord_id: str) -> ProfileView:
        value = services()
        account = await value.auth.account(discord_id)
        role = account.role
        if role is None:
            raise AuthError("profile_not_found", 404)
        return ProfileView(
            discord_id=account.discord_id,
            profile=account.profile,
            profile_complete=account.profile_complete,
            role=role,
            members=tuple(
                DiscordMemberView.model_validate(member)
                for member in value.directory.members()
                if member.discord_id == discord_id
            ),
        )

    @app.get("/api/channels")
    async def channels() -> list[ChannelView]:
        return [
            ChannelView.model_validate(
                asdict(channel)
                | {
                    "id": str(channel.id),
                    "guild_id": str(channel.guild_id),
                }
            )
            for channel in services().voice.channels()
        ]

    @app.get(
        "/api/events",
        response_class=EventSourceResponse,
        response_model=SessionView | ChangeView | ApiError,
        openapi_extra={
            "x-sse-payloads": {
                "state": {"$ref": "#/components/schemas/SessionView"},
                "change": {"$ref": "#/components/schemas/ChangeView"},
                "auth": {"$ref": "#/components/schemas/ApiError"},
            }
        },
    )
    async def events(request: Request) -> AsyncGenerator[ServerSentEvent]:
        async for event in event_stream(
            services(), request.cookies.get(SESSION_COOKIE)
        ):
            yield event

    mutation_errors: dict[int | str, dict[str, object]] = {
        status: {**error_responses[status], "model": MutationView | ApiError}
        for status in (404, 409, 422)
    }

    @app.post("/api/queue", response_model=MutationView, responses=mutation_errors)
    async def add(
        body: AddInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(Add(body.track_ids, body.skip_duplicates), operation, user)

    @app.delete(
        "/api/queue/{entry_id}", response_model=MutationView, responses=mutation_errors
    )
    async def remove(
        entry_id: UUID, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(Remove(entry_id), operation, user)

    @app.put(
        "/api/queue/{entry_id}/position",
        response_model=MutationView,
        responses=mutation_errors,
    )
    async def move(
        entry_id: UUID, body: MoveInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(
            Move(entry_id, body.before_entry_id, body.expected_queue_revision),
            operation,
            user,
        )

    @app.post(
        "/api/queue/clear", response_model=MutationView, responses=mutation_errors
    )
    async def clear(
        body: ClearInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(
            Clear(body.expected_queue_revision, body.contributor_id), operation, user
        )

    @app.post(
        "/api/queue/undo/{undo_id}",
        response_model=MutationView,
        responses=mutation_errors,
    )
    async def undo(
        undo_id: UUID, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(Undo(undo_id), operation, user)

    @app.post(
        "/api/playback/control", response_model=MutationView, responses=mutation_errors
    )
    async def control(
        body: ControlInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        if (
            body.action in {"pause", "skip", "stop"}
            and body.expected_attempt_id is None
        ):
            raise ValueError("This control needs the current playback attempt.")
        return await mutate(
            Control(body.action), operation, user, attempt=body.expected_attempt_id
        )

    @app.put(
        "/api/playback/position", response_model=MutationView, responses=mutation_errors
    )
    async def seek(
        body: SeekInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(
            Seek(body.seconds), operation, user, attempt=body.expected_attempt_id
        )

    @app.put(
        "/api/playback/volume", response_model=MutationView, responses=mutation_errors
    )
    async def volume(
        body: VolumeInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(SetVolume(body.volume), operation, user)

    @app.put(
        "/api/playback/crossfade",
        response_model=MutationView,
        responses=mutation_errors,
    )
    async def crossfade(
        body: CrossfadeInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(SetCrossfade(body.seconds), operation, user)

    @app.put("/api/connection", response_model=MutationView, responses=mutation_errors)
    async def join(
        body: JoinInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        channel_id = int(body.channel_id)
        if channel_id > 2**63 - 1:
            raise ValueError("Channel ID exceeds the supported integer range.")
        return await mutate(Join(channel_id), operation, user)

    @app.post("/api/radio", response_model=MutationView, responses=mutation_errors)
    async def radio(
        body: RadioInput, operation: OperationID, user: CurrentUser
    ) -> JSONResponse:
        return await mutate(
            StartRadio(body.seed, body.expected_generation), operation, user
        )

    @app.post(
        "/api/radio/{generation}/{action}",
        response_model=MutationView,
        responses=mutation_errors,
    )
    async def radio_control(
        generation: UUID,
        action: Literal["stop", "retry"],
        operation: OperationID,
        user: CurrentUser,
    ) -> JSONResponse:
        return await mutate(
            StopRadio(generation) if action == "stop" else RetryRadio(generation),
            operation,
            user,
        )

    @app.get("/api/catalog/search")
    async def search(
        q: Annotated[str, Query(min_length=1, max_length=200)],
        provider: str = "youtube_music",
        refresh: bool = False,
    ) -> DiscoveryView:
        value = await services().catalog.search(
            q, provider_key=provider, refresh=refresh
        )
        return await discovery_document(value.version, value.value, services().catalog)

    @app.post("/api/catalog/playlist")
    async def playlist(body: PlaylistInput) -> DiscoveryView:
        value = await services().catalog.playlist(
            body.source_url, provider_key=body.provider, refresh=body.refresh
        )
        return await discovery_document(value.version, value.value, services().catalog)

    @app.post("/api/catalog/track")
    async def track(body: LinkInput) -> TrackView:
        value = await services().catalog.track(
            body.source_url, provider_key=body.provider
        )
        return TrackView.model_validate(value)

    @app.get("/api/catalog/{kind}/{version}")
    async def snapshot(
        kind: Literal["search", "playlist"],
        version: UUID,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> DiscoveryView:
        catalog = services().catalog
        value = (
            catalog.playlist_snapshot(version).value
            if kind == "playlist"
            else catalog.search_snapshot(version).value
        )
        return await discovery_document(
            version,
            value,
            catalog,
            offset=offset,
            limit=limit,
        )

    async def discovery_document(
        version: UUID,
        value: TrackPage | PlaylistPage,
        catalog: Catalog,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> DiscoveryView:
        playlist = value if isinstance(value, PlaylistPage) else None
        page = value.page if isinstance(value, PlaylistPage) else value
        visible = page.entries[offset : offset + limit]
        tracks = await services().metadata.get_many(
            tuple(
                entry.reference.identity
                for entry in visible
                if isinstance(entry, TrackFinding)
            )
        )
        by_identity = {track.identity: track.id for track in tracks}
        next_offset = offset + len(visible)
        return DiscoveryView(
            version=version,
            offset=offset,
            total=len(page.entries),
            next_offset=next_offset if next_offset < len(page.entries) else None,
            source_has_more=page.continuation is not None,
            error=page.error,
            playlist=PlaylistView.model_validate(playlist) if playlist else None,
            entries=tuple(
                CatalogEntryView(
                    position=offset + index,
                    track_id=by_identity[entry.reference.identity]
                    if isinstance(entry, TrackFinding)
                    else None,
                    reference=entry.reference,
                    metadata=MetadataView.model_validate(entry.metadata),
                    unavailable=None
                    if isinstance(entry, TrackFinding)
                    else entry.reason,
                )
                for index, entry in enumerate(visible)
            ),
            refresh=catalog.refresh_status(version, playlist=playlist is not None),
        )

    return app

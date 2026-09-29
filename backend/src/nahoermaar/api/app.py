# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""FastAPI application assembled around the composition root."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from nahoermaar.catalog.service import CatalogError
from nahoermaar.bootstrap import Application, bootstrap
from nahoermaar.library.domain import LibraryError
from nahoermaar.lyrics.service import LyricsError
from nahoermaar.player.domain import PlayerError
from nahoermaar.users.domain import AuthError

from .auth import router as auth_router
from .catalog import router as catalog_router
from .errors import ApiError, ApiErrorCode, ErrorView, error_responses
from .events import router as events_router
from .logs import router as logs_router
from .lyrics import router as lyrics_router
from .jobs import router as jobs_router
from .incidents import router as incidents_router
from .library import router as library_router
from .middleware import install_auth_middleware
from .player import router as player_router
from .playbacks import router as playbacks_router
from .statistics import router as statistics_router
from .users import router as users_router

_LOGGER = logging.getLogger(__name__)


def create_app(application: Application | None = None) -> FastAPI:
    """Create one API process around an explicit application container."""
    container = application or bootstrap()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            await container.start()
            yield
        finally:
            await container.close()

    app = FastAPI(
        title="NaHörMaar",
        lifespan=lifespan,
        responses=error_responses(500),
    )
    app.state.application = container
    install_error_handlers(app)
    install_auth_middleware(
        app,
        container.users.auth,
        container.settings.auth,
        container.operations.incidents,
    )
    app.include_router(auth_router(container))
    app.include_router(users_router(container))
    app.include_router(catalog_router(container.catalog.service))
    app.include_router(library_router(container))
    app.include_router(lyrics_router(container.lyrics.service))
    app.include_router(player_router(container))
    app.include_router(playbacks_router(container))
    app.include_router(statistics_router(container))
    app.include_router(events_router(container))
    app.include_router(logs_router(container))
    app.include_router(jobs_router(container))
    app.include_router(incidents_router(container))

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks = {
            "database": "ready",
            "player": (
                "ready" if container.player.service.operational else "unavailable"
            ),
            "listening": (
                "ready" if container.listening.service.operational else "unavailable"
            ),
        }
        try:
            async with asyncio.timeout(2):
                await container.database.ping()
        except (TimeoutError, SQLAlchemyError):
            checks["database"] = "unavailable"
            _LOGGER.warning("application.readiness_failed", exc_info=True)

        if container.settings.discord.enabled:
            checks["discord"] = (
                "ready"
                if container.integrations.gateway is not None
                and container.integrations.gateway.operational
                else "unavailable"
            )
            checks["playback"] = (
                "ready"
                if container.player.playback is not None
                and container.player.playback.operational
                else "unavailable"
            )
        else:
            checks["discord"] = "disabled"
            checks["playback"] = "disabled"

        status = (
            "ready"
            if all(state in {"ready", "disabled"} for state in checks.values())
            else "unavailable"
        )
        body: dict[str, str | dict[str, str]] = {
            "status": status,
            "checks": checks,
        }
        return JSONResponse(body, status_code=200 if status == "ready" else 503)

    return app


def install_error_handlers(app: FastAPI) -> None:
    """Expose stable error documents without leaking exception details."""

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError) -> JSONResponse:
        return _error_response(request, error.code.value, error.status)

    @app.exception_handler(PlayerError)
    async def player_error(request: Request, error: PlayerError) -> JSONResponse:
        return _error_response(request, error.code.value, error.status)

    @app.exception_handler(CatalogError)
    async def catalog_error(request: Request, error: CatalogError) -> JSONResponse:
        return _error_response(
            request,
            error.code.value,
            error.status,
            retryable=error.retryable,
        )

    @app.exception_handler(LyricsError)
    async def lyrics_error(request: Request, error: LyricsError) -> JSONResponse:
        headers = (
            {"retry-after": str(error.retry_after_seconds)}
            if error.retry_after_seconds is not None
            else None
        )
        return _error_response(
            request,
            error.code.value,
            error.status,
            retryable=error.retryable,
            headers=headers,
        )

    @app.exception_handler(LibraryError)
    async def library_error(request: Request, error: LibraryError) -> JSONResponse:
        return _error_response(request, error.code.value, error.status)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error_response(
            request,
            error.code.value,
            error.status,
            retryable=error.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            request,
            ApiErrorCode.VALIDATION_FAILED,
            422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        code = {
            400: ApiErrorCode.INVALID_REQUEST,
            401: ApiErrorCode.AUTHENTICATION_REQUIRED,
            403: ApiErrorCode.ACCESS_DENIED,
            404: ApiErrorCode.NOT_FOUND,
            405: ApiErrorCode.METHOD_NOT_ALLOWED,
            408: ApiErrorCode.REQUEST_TIMEOUT,
            409: ApiErrorCode.CONFLICT,
            422: ApiErrorCode.VALIDATION_FAILED,
            429: ApiErrorCode.RATE_LIMITED,
            502: ApiErrorCode.UPSTREAM_FAILED,
            503: ApiErrorCode.SERVICE_UNAVAILABLE,
            504: ApiErrorCode.GATEWAY_TIMEOUT,
        }.get(
            error.status_code,
            (
                ApiErrorCode.INVALID_REQUEST
                if error.status_code < 500
                else ApiErrorCode.INTERNAL_ERROR
            ),
        )
        return _error_response(
            request,
            code,
            error.status_code,
            retryable=(
                error.status_code in {408, 423, 425, 429} or error.status_code >= 500
            ),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _error: Exception) -> JSONResponse:
        return _error_response(
            request,
            ApiErrorCode.INTERNAL_ERROR,
            500,
            retryable=True,
        )


def _error_response(
    request: Request,
    code: str | StrEnum,
    status: int,
    *,
    retryable: bool | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    value = code.value if isinstance(code, StrEnum) else code
    request.state.error_code = value
    request.state.error_retryable = retryable
    return JSONResponse(
        ErrorView(code=value, retryable=retryable).model_dump(exclude_none=True),
        status_code=status,
        headers={"cache-control": "no-store", **(headers or {})},
    )

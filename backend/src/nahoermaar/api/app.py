# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""FastAPI application assembled around the composition root."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from nahoermaar.catalog.service import CatalogError
from nahoermaar.bootstrap import Application, bootstrap
from nahoermaar.player.domain import PlayerError
from nahoermaar.users.domain import AuthError

from .auth import router as auth_router
from .catalog import router as catalog_router
from .errors import ERROR_RESPONSES, ErrorView
from .events import router as events_router
from .listening import router as listening_router
from .logs import router as logs_router
from .middleware import install_auth_middleware
from .player import router as player_router
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
        responses=ERROR_RESPONSES,
    )
    app.state.application = container
    install_auth_middleware(app, container.auth, container.settings.auth)
    app.include_router(auth_router(container))
    app.include_router(users_router(container))
    app.include_router(catalog_router(container.catalog))
    app.include_router(player_router(container))
    app.include_router(listening_router(container.recent))
    app.include_router(statistics_router(container))
    app.include_router(events_router(container))
    app.include_router(logs_router(container))

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError) -> JSONResponse:
        request.state.error_code = error.code.value
        return JSONResponse(
            ErrorView(error=error.code.value).model_dump(exclude_none=True),
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(PlayerError)
    async def player_error(request: Request, error: PlayerError) -> JSONResponse:
        request.state.error_code = error.code.value
        return JSONResponse(
            ErrorView(error=error.code.value).model_dump(exclude_none=True),
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(CatalogError)
    async def catalog_error(request: Request, error: CatalogError) -> JSONResponse:
        request.state.error_code = error.code.value
        request.state.error_retryable = error.retryable
        return JSONResponse(
            ErrorView(
                error=error.code.value,
                retryable=error.retryable,
            ).model_dump(exclude_none=True),
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        request.state.error_code = "validation_failed"
        return JSONResponse(
            ErrorView(error="validation_failed").model_dump(exclude_none=True),
            status_code=422,
            headers={"cache-control": "no-store"},
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks = {
            "database": "ready",
            "player": "ready" if container.player.operational else "unavailable",
            "listening": (
                "ready" if container.listening.operational else "unavailable"
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
                if container.gateway is not None and container.gateway.operational
                else "unavailable"
            )
            checks["playback"] = (
                "ready"
                if container.playback is not None and container.playback.operational
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

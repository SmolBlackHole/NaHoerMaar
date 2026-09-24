# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""FastAPI application assembled around the composition root."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nahoermaar.catalog.service import CatalogError
from nahoermaar.bootstrap import Application, bootstrap
from nahoermaar.player.domain import PlayerError
from nahoermaar.users.domain import AuthError

from .auth import router as auth_router
from .catalog import router as catalog_router
from .events import router as events_router
from .middleware import install_auth_middleware
from .player import router as player_router
from .users import router as users_router


def create_app(application: Application | None = None) -> FastAPI:
    """Create one API process around an explicit application container."""
    container = application or bootstrap()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        await container.start()
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title="NaHÃ¶rMaar", lifespan=lifespan)
    app.state.application = container
    install_auth_middleware(app, container.auth, container.settings.auth)
    app.include_router(auth_router(container))
    app.include_router(users_router(container))
    app.include_router(catalog_router(container.catalog))
    app.include_router(player_router(container))
    app.include_router(events_router(container))

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse(
            {"error": error.code.value},
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(PlayerError)
    async def player_error(_request: Request, error: PlayerError) -> JSONResponse:
        return JSONResponse(
            {"error": error.code.value},
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.exception_handler(CatalogError)
    async def catalog_error(_request: Request, error: CatalogError) -> JSONResponse:
        return JSONResponse(
            {"error": error.code.value, "retryable": error.retryable},
            status_code=error.status,
            headers={"cache-control": "no-store"},
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app

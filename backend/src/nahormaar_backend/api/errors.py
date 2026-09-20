# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Translate service failures into the existing HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from ..application.audio import TrackError
from ..cache import CatalogBusy
from ..domain.identity import AuthError
from ..persistence.player_store import StorageError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def account_storage_failed(
        request: Request, error: SQLAlchemyError
    ) -> JSONResponse:
        return JSONResponse({"code": "auth_unavailable"}, status_code=503)

    @app.exception_handler(AuthError)
    async def auth_failed(request: Request, error: AuthError) -> JSONResponse:
        return JSONResponse({"code": error.code}, status_code=error.status)

    @app.exception_handler(CatalogBusy)
    async def discovery_busy(request: Request, error: CatalogBusy) -> JSONResponse:
        return JSONResponse(
            {"detail": str(error)}, status_code=429, headers={"Retry-After": "5"}
        )

    @app.exception_handler(TrackError)
    async def discovery_failed(request: Request, error: TrackError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=502)

    @app.exception_handler(StorageError)
    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"code": "backend_unavailable"}, status_code=503)

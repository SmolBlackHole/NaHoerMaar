# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Canonical-origin, browser-session and CSRF enforcement."""

from collections.abc import Awaitable, Callable
import secrets

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from nahoermaar.config import AuthSettings, CALLBACK_PATH
from nahoermaar.users.domain import Authenticated, AuthError, AuthErrorCode
from nahoermaar.users.service import AuthService, SESSION_COOKIE

type RequestHandler = Callable[[Request], Awaitable[Response]]

_PUBLIC_PATHS = {"/api/auth/discord", CALLBACK_PATH}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def install_auth_middleware(
    app: FastAPI,
    auth: AuthService,
    settings: AuthSettings,
) -> None:
    """Install the one browser security boundary for every API route."""

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestHandler) -> Response:
        request_id = secrets.token_hex(8)
        path = request.url.path
        if not path.startswith("/api/"):
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response

        origin = request.headers.get("origin")
        if origin is not None and origin != settings.public_origin:
            return _error(AuthErrorCode.ORIGIN_FORBIDDEN, 403, request_id)

        if path not in _PUBLIC_PATHS:
            try:
                authenticated = await auth.authenticate(
                    request.cookies.get(SESSION_COOKIE)
                )
            except AuthError as error:
                return _error(error.code, error.status, request_id)
            request.state.authenticated = authenticated
            if request.method not in _SAFE_METHODS and (
                origin != settings.public_origin
                or not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""),
                    authenticated.csrf,
                )
            ):
                return _error(AuthErrorCode.CSRF_FAILED, 403, request_id)

        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["vary"] = "Cookie"
        response.headers["x-request-id"] = request_id
        return response


def authenticated(request: Request) -> Authenticated:
    """Return the session established by the auth middleware."""
    value = getattr(request.state, "authenticated", None)
    if not isinstance(value, Authenticated):
        raise AuthError(AuthErrorCode.SIGNED_OUT, 401)
    return value


def _error(
    code: AuthErrorCode,
    status: int,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        {"error": code.value},
        status_code=status,
        headers={
            "cache-control": "no-store",
            "referrer-policy": "no-referrer",
            "vary": "Cookie",
            "x-request-id": request_id,
        },
    )

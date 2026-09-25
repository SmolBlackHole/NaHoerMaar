# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""One HTTP security and observability boundary for the API."""

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from nahoermaar.config import AuthSettings, CALLBACK_PATH
from nahoermaar.observability import LogContext, log_context
from nahoermaar.users.domain import Authenticated, AuthError, AuthErrorCode
from nahoermaar.users.service import AuthService, SESSION_COOKIE

type RequestHandler = Callable[[Request], Awaitable[Response]]

_LOGGER = logging.getLogger(__name__)
_PUBLIC_PATHS = {"/api/auth/discord", CALLBACK_PATH}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def install_auth_middleware(
    app: FastAPI,
    auth: AuthService,
    settings: AuthSettings,
) -> None:
    """Install the one browser security and request logging boundary."""

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestHandler) -> Response:
        request_uuid = uuid4()
        request_id = str(request_uuid)
        path = request.url.path
        started = time.perf_counter()
        actor_id: UUID | None = None
        request.state.request_id = request_id
        request.state.correlation_id = request_uuid

        with log_context(
            LogContext(request_id=request_id, correlation_id=request_uuid)
        ):
            try:
                if not path.startswith("/api/"):
                    response = await call_next(request)
                    response.headers["x-request-id"] = request_id
                    _log_completed(request, response.status_code, started)
                    return response

                origin = request.headers.get("origin")
                if origin is not None and origin != settings.public_origin:
                    response = _error(AuthErrorCode.ORIGIN_FORBIDDEN, 403, request_id)
                    _log_rejected(
                        request,
                        response.status_code,
                        started,
                        AuthErrorCode.ORIGIN_FORBIDDEN,
                    )
                    return response

                if path not in _PUBLIC_PATHS:
                    try:
                        current = await auth.authenticate(
                            request.cookies.get(SESSION_COOKIE)
                        )
                    except AuthError as error:
                        response = _error(error.code, error.status, request_id)
                        _log_rejected(
                            request, response.status_code, started, error.code
                        )
                        return response
                    actor_id = current.user.id
                    request.state.authenticated = current
                    if request.method not in _SAFE_METHODS and (
                        origin != settings.public_origin
                        or not secrets.compare_digest(
                            request.headers.get("x-csrf-token", ""),
                            current.csrf,
                        )
                    ):
                        with log_context(LogContext(actor_id=actor_id)):
                            response = _error(
                                AuthErrorCode.CSRF_FAILED, 403, request_id
                            )
                            _log_rejected(
                                request,
                                response.status_code,
                                started,
                                AuthErrorCode.CSRF_FAILED,
                            )
                        return response

                with log_context(LogContext(actor_id=actor_id)):
                    response = await call_next(request)
                    _secure(response, request_id)
                    _log_completed(request, response.status_code, started)
                    return response
            except Exception:
                with log_context(LogContext(actor_id=actor_id)):
                    _LOGGER.exception(
                        "http.request_failed method=%s path=%s status=500 duration_ms=%.2f",
                        request.method,
                        path,
                        _elapsed_ms(started),
                    )
                raise


def authenticated(request: Request) -> Authenticated:
    """Return the session established by the auth middleware."""
    value = getattr(request.state, "authenticated", None)
    if not isinstance(value, Authenticated):
        raise AuthError(AuthErrorCode.SIGNED_OUT, 401)
    return value


def _error(code: AuthErrorCode, status: int, request_id: str) -> JSONResponse:
    response = JSONResponse({"error": code.value}, status_code=status)
    _secure(response, request_id)
    return response


def _secure(response: Response, request_id: str) -> None:
    response.headers["cache-control"] = "no-store"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["vary"] = "Cookie"
    response.headers["x-request-id"] = request_id


def _log_completed(request: Request, status: int, started: float) -> None:
    level = logging.WARNING if status >= 500 else logging.INFO
    _LOGGER.log(
        level,
        "http.request_completed method=%s path=%s status=%d duration_ms=%.2f "
        "error_code=%s retryable=%s",
        request.method,
        request.url.path,
        status,
        _elapsed_ms(started),
        getattr(request.state, "error_code", None),
        getattr(request.state, "error_retryable", None),
    )


def _log_rejected(
    request: Request,
    status: int,
    started: float,
    code: AuthErrorCode,
) -> None:
    level = (
        logging.WARNING
        if code
        in {
            AuthErrorCode.ORIGIN_FORBIDDEN,
            AuthErrorCode.CSRF_FAILED,
        }
        else logging.INFO
    )
    _LOGGER.log(
        level,
        "http.request_rejected method=%s path=%s status=%d duration_ms=%.2f error_code=%s",
        request.method,
        request.url.path,
        status,
        _elapsed_ms(started),
        code.value,
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Session and origin checks shared by every API endpoint."""

import secrets
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..application.auth import SESSION_COOKIE, Auth
from ..config import CALLBACK_PATH
from ..domain.identity import AuthError

PUBLIC_AUTH_PATHS = {"/api/auth/discord", CALLBACK_PATH}


class AuthBoundary:
    def __init__(self, app: ASGIApp, service: Callable[[], Auth]) -> None:
        self.app, self.service = app, service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message["headers"]
                    if key.lower() != b"cache-control"
                ] + [
                    (b"cache-control", b"no-store"),
                    (b"vary", b"Cookie"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        request = Request(scope)
        try:
            auth = self.service()
            origin = request.headers.get("origin")
            if origin is not None and origin != auth.settings.public_origin:
                raise AuthError("origin_forbidden", 403)
            if scope["path"] not in PUBLIC_AUTH_PATHS:
                user = await auth.authenticate(
                    request.cookies.get(SESSION_COOKIE),
                    check_access=scope["path"] != "/api/auth/logout",
                )
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    if (
                        origin != auth.settings.public_origin
                        or not secrets.compare_digest(
                            request.headers.get("x-csrf-token", ""), user.csrf
                        )
                    ):
                        raise AuthError("csrf_failed", 403)
                request.state.user = user
        except AuthError as exc:
            await JSONResponse({"code": exc.code}, status_code=exc.status)(
                scope, receive, private_send
            )
            return
        except SQLAlchemyError:
            await JSONResponse({"code": "auth_unavailable"}, status_code=503)(
                scope, receive, private_send
            )
            return
        await self.app(scope, receive, private_send)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authentication boundary and browser session endpoints."""

import secrets
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import (
    Auth,
    AuthError,
    Authenticated,
    CALLBACK_PATH,
    LOGIN_COOKIE,
    SESSION_COOKIE,
    SESSION_SECONDS,
)

PUBLIC_AUTH_PATHS = {"/api/auth/discord", CALLBACK_PATH}


def current_user(request: Request) -> Authenticated:
    return cast(Authenticated, request.state.user)


type CurrentUser = Annotated[Authenticated, Depends(current_user)]


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


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=32)
    avatar: str = Field(pattern=r"^[0-9a-f]{4}$")


def session_data(user: Authenticated) -> dict[str, object]:
    return {
        "profile": asdict(user.account.profile),
        "profile_complete": user.account.profile_complete,
        "csrf_token": user.csrf,
        "expires_at": user.expires_at,
    }


def auth_router(service: Callable[[], Auth]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/auth/discord")
    async def login(request: Request) -> Response:
        auth = service()
        try:
            url, browser = await auth.begin(request.cookies.get(LOGIN_COOKIE))
        except AuthError as exc:
            return RedirectResponse(f"/login?error={exc.code}", status_code=303)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE,
            browser,
            max_age=600,
            httponly=True,
            secure=auth.settings.secure,
            samesite="lax",
            path="/api/auth",
        )
        return response

    @router.get(CALLBACK_PATH)
    async def callback(request: Request) -> Response:
        auth = service()
        query = request.query_params
        response: Response
        try:
            token = await auth.callback(
                query.get("state"),
                request.cookies.get(LOGIN_COOKIE),
                query.get("code"),
                query.get("error"),
                request.cookies.get(SESSION_COOKIE),
            )
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE,
                token,
                max_age=SESSION_SECONDS,
                httponly=True,
                secure=auth.settings.secure,
                samesite="lax",
                path="/api",
            )
        except AuthError as exc:
            response = RedirectResponse(f"/login?error={exc.code}", status_code=303)
        response.delete_cookie(
            LOGIN_COOKIE,
            path="/api/auth",
            secure=auth.settings.secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/api/auth/session")
    async def session(user: CurrentUser) -> dict[str, object]:
        return session_data(user)

    @router.post("/api/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        auth = service()
        await auth.logout(request.cookies[SESSION_COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/api",
            secure=auth.settings.secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.put("/api/profile")
    async def profile(body: ProfileInput, user: CurrentUser) -> dict[str, object]:
        account = await service().profile(user, body.name, body.avatar)
        return session_data(Authenticated(account, user.expires_at, user.csrf))

    return router

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""HTTP authentication boundary for the isolated engine application.

Keep the existing Auth service, cookies, whitelist and CSRF rules. No legacy
player API or application runtime is imported by this boundary.
"""

import logging
import secrets
from collections.abc import Callable
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..application.auth import (
    Auth,
    Authenticated,
    LOGIN_COOKIE,
    SESSION_COOKIE,
    SESSION_SECONDS,
)
from ..config import CALLBACK_PATH
from ..domain.identity import AuthError
from ..domain.preferences import Appearance
from .api_models import AccountView, ApiError

_LOGGER = logging.getLogger(__name__)


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
            if scope["path"] not in {"/api/auth/discord", CALLBACK_PATH}:
                user = await auth.authenticate(
                    request.cookies.get(SESSION_COOKIE),
                    check_access=scope["path"] != "/api/auth/logout",
                )
                if request.method not in ("GET", "HEAD", "OPTIONS") and (
                    origin != auth.settings.public_origin
                    or not secrets.compare_digest(
                        request.headers.get("x-csrf-token", ""), user.csrf
                    )
                ):
                    raise AuthError("csrf_failed", 403)
                request.state.user = user
        except AuthError as error:
            if error.code in {"access_denied", "origin_forbidden", "csrf_failed"}:
                _LOGGER.warning(
                    "auth.request_rejected method=%s path=%s reason=%s",
                    request.method,
                    scope["path"],
                    error.code,
                )
            await JSONResponse(
                ApiError(code=error.code).model_dump(), status_code=error.status
            )(scope, receive, private_send)
            return
        except SQLAlchemyError:
            await JSONResponse(
                ApiError(code="auth_unavailable", retryable=True).model_dump(),
                status_code=503,
            )(scope, receive, private_send)
            return
        await self.app(scope, receive, private_send)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=32)
    avatar: str = Field(pattern=r"^[0-9a-f]{4}$")


def account_document(user: Authenticated) -> AccountView:
    return AccountView(
        profile=user.account.profile,
        profile_complete=user.account.profile_complete,
        is_admin=user.admin,
        csrf_token=user.csrf,
        expires_at=user.expires_at,
        appearance=user.account.appearance,
    )


def auth_router(service: Callable[[], Auth]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/auth/discord")
    async def login(request: Request) -> Response:
        auth = service()
        try:
            url, browser = await auth.begin(request.cookies.get(LOGIN_COOKIE))
        except AuthError as error:
            _LOGGER.warning("auth.login_start_failed reason=%s", error.code)
            return RedirectResponse(f"/login?error={error.code}", status_code=303)
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
        auth, query = service(), request.query_params
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
        except AuthError as error:
            _LOGGER.warning("auth.login_failed reason=%s", error.code)
            response = RedirectResponse(f"/login?error={error.code}", status_code=303)
        response.delete_cookie(
            LOGIN_COOKIE,
            path="/api/auth",
            secure=auth.settings.secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/api/auth/session")
    async def account(user: CurrentUser) -> AccountView:
        return account_document(user)

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
    async def profile(body: ProfileInput, user: CurrentUser) -> AccountView:
        account = await service().profile(user, body.name, body.avatar)
        return account_document(
            Authenticated(account, user.expires_at, user.csrf, user.admin)
        )

    @router.put("/api/profile/appearance")
    async def appearance(body: Appearance, user: CurrentUser) -> Appearance:
        return await service().appearance(user, body)

    return router

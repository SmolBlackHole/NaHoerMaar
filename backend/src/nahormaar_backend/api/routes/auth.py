# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord login, logout and account endpoints."""

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from ...application.auth import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    SESSION_SECONDS,
    Auth,
    Authenticated,
)
from ...config import CALLBACK_PATH
from ...domain.identity import AuthError
from ...domain.preferences import Appearance
from ..dependencies import CurrentUser
from ..schemas import ProfileInput


def session_data(user: Authenticated) -> dict[str, object]:
    return {
        "profile": asdict(user.account.profile),
        "profile_complete": user.account.profile_complete,
        "csrf_token": user.csrf,
        "expires_at": user.expires_at,
        "appearance": user.account.appearance.model_dump(),
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

    @router.put("/api/profile/appearance")
    async def appearance(body: Appearance, user: CurrentUser) -> Appearance:
        return await service().appearance(user, body)

    return router

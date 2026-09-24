# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord OAuth and browser-session endpoints."""

from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from nahoermaar.bootstrap import Application
from nahoermaar.users.domain import AccessRole, AuthError
from nahoermaar.users.service import (
    BeginLogin,
    CompleteLogin,
    LOGIN_COOKIE,
    LOGIN_LIFETIME,
    Logout,
    SESSION_COOKIE,
    SESSION_LIFETIME,
)

from .middleware import authenticated


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    discord_id: str
    role: AccessRole
    profile_complete: bool
    expires_at: datetime
    csrf: str


def router(application: Application) -> APIRouter:
    """Build auth routes around the composed application."""
    routes = APIRouter(prefix="/api/auth", tags=["auth"])
    settings = application.settings.auth

    @routes.get("/discord", response_class=RedirectResponse)
    async def begin(request: Request) -> RedirectResponse:
        result = await application.bus.execute(
            BeginLogin(request.cookies.get(LOGIN_COOKIE))
        )
        response = RedirectResponse(result.authorization_url, status_code=302)
        response.set_cookie(
            LOGIN_COOKIE,
            result.browser_token,
            max_age=int(LOGIN_LIFETIME.total_seconds()),
            httponly=True,
            secure=settings.secure,
            samesite="lax",
            path="/api/auth",
        )
        return response

    @routes.get("/discord/callback", response_class=RedirectResponse)
    async def callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        try:
            result = await application.bus.execute(
                CompleteLogin(
                    state,
                    request.cookies.get(LOGIN_COOKIE),
                    code,
                    error,
                    request.cookies.get(SESSION_COOKIE),
                )
            )
        except AuthError as auth_error:
            response = RedirectResponse(
                f"{settings.public_origin}/login?error={quote(auth_error.code.value)}",
                status_code=302,
            )
            response.delete_cookie(LOGIN_COOKIE, path="/api/auth")
            return response

        target = "/" if result.user.profile_complete else "/profile"
        response = RedirectResponse(settings.public_origin + target, status_code=302)
        response.delete_cookie(LOGIN_COOKIE, path="/api/auth")
        response.set_cookie(
            SESSION_COOKIE,
            result.session_token,
            max_age=int(SESSION_LIFETIME.total_seconds()),
            httponly=True,
            secure=settings.secure,
            samesite="lax",
            path="/api",
        )
        return response

    @routes.get("/session")
    async def session(request: Request) -> SessionView:
        current = authenticated(request)
        role = current.user.role
        if role is None:
            raise AssertionError("Authenticated users always have an access role.")
        return SessionView(
            user_id=current.user.id,
            discord_id=current.user.discord.discord_id,
            role=role,
            profile_complete=current.user.profile_complete,
            expires_at=current.expires_at,
            csrf=current.csrf,
        )

    @routes.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        await application.bus.execute(Logout(request.cookies.get(SESSION_COOKIE)))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/api")
        return response

    return routes

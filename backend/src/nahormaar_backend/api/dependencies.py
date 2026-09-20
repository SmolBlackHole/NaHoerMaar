# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Typed access to the application's running services."""

from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request

from ..application.auth import Auth, Authenticated
from ..application.catalog import MediaCatalog
from ..application.playback import PlaybackController
from ..domain.identity import AuthError


@dataclass(slots=True)
class ApiServices:
    controller: PlaybackController | None = None
    authentication: Auth | None = None

    def auth(self) -> Auth:
        if self.authentication is None:
            raise AuthError("auth_unavailable", 503)
        return self.authentication

    async def player(self) -> PlaybackController:
        if self.controller is None:
            raise HTTPException(503, detail="Backend is not running.")
        return self.controller

    async def catalog(self) -> MediaCatalog:
        active = await self.player()
        if active.catalog is None:
            raise HTTPException(503, detail="YouTube discovery is not running.")
        return active.catalog


def current_user(request: Request) -> Authenticated:
    return cast(Authenticated, request.state.user)


type CurrentUser = Annotated[Authenticated, Depends(current_user)]

type RequestID = Annotated[UUID, Header(alias="Idempotency-Key")]

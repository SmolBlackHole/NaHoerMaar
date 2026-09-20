# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated radio previews and serialized radio controls."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response

from ...application.playback import PlaybackController
from ...application.radio import RadioPreview
from ...domain.commands import RetryRadio, StartRadio, StopRadio
from ...integrations.youtube_radio import radio_seed
from .. import schemas as dto
from ..dependencies import ApiServices, CurrentUser, RequestID
from ..mutations import mutate


def radio_router(services: ApiServices) -> APIRouter:
    router = APIRouter()

    @router.post("/api/radio/preview")
    async def preview(
        user: CurrentUser,
        body: dto.RadioPreviewInput,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(services.player)],
    ) -> RadioPreview:
        if active.radio_catalog is None:
            raise HTTPException(503, detail="Radio is unavailable.")
        try:
            seed = radio_seed(body.source_url, body.kind, body.title)
            return await active.radio_catalog.preview(
                request_id, user.account.profile.id, seed
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @router.post("/api/radio/start")
    async def start(
        user: CurrentUser,
        body: dto.RadioStartInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            StartRadio(body.preview_id, body.expected_session_id),
            response,
            active,
        )

    @router.post("/api/radio/{action}")
    async def control(
        action: Literal["stop", "retry"],
        user: CurrentUser,
        body: dto.RadioSessionInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[PlaybackController, Depends(services.player)],
    ) -> dto.MutationResult:
        command = (
            StopRadio(body.expected_session_id)
            if action == "stop"
            else RetryRadio(body.expected_session_id)
        )
        return await mutate(user, request_id, command, response, active)

    return router

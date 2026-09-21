# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Player, voice and snapshot endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response

from ...application.session import Session
from ...domain import commands
from .. import schemas as dto
from ..dependencies import ApiServices, CurrentUser, RequestID
from ..mutations import mutate


def player_router(services: ApiServices) -> APIRouter:
    router = APIRouter()

    async def state(
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.State:
        return dto.State.from_status(await active.read_status())

    router.get("/api/state", response_model=dto.State)(state)

    @router.get("/api/channels")
    async def channels(
        active: Annotated[Session, Depends(services.player)],
    ) -> list[dto.Channel]:
        await active.read_status()
        return [
            dto.Channel(
                id=str(channel.id),
                name=channel.name,
                can_connect=channel.can_connect,
                can_speak=channel.can_speak,
                guild_id=str(channel.guild_id),
                guild_name=channel.guild_name,
            )
            for channel in active.channels()
        ]

    @router.post("/api/player/{action}")
    async def control(
        user: CurrentUser,
        action: Literal["play", "pause", "skip", "stop"],
        body: dto.ControlInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Control(action, body.expected_playback_id),
            response,
            active,
        )

    @router.put("/api/player/volume")
    async def volume(
        user: CurrentUser,
        body: dto.VolumeInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Volume(body.volume), response, active
        )

    @router.put("/api/player/crossfade")
    async def crossfade(
        user: CurrentUser,
        body: dto.CrossfadeInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Crossfade(body.seconds), response, active
        )

    @router.put("/api/player/seek")
    async def seek(
        user: CurrentUser,
        body: dto.SeekInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Seek(body.position_seconds, body.expected_playback_id),
            response,
            active,
        )

    @router.put("/api/voice/channel")
    async def connect(
        user: CurrentUser,
        body: dto.ChannelInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Connect(int(body.channel_id)), response, active
        )

    @router.delete("/api/voice/channel")
    async def disconnect(
        user: CurrentUser,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(user, request_id, commands.Disconnect(), response, active)

    return router

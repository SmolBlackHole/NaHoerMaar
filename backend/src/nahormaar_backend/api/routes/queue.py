# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Queue mutations addressed by entry and request IDs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ...application.session import Session
from ...domain import commands
from .. import schemas as dto
from ..dependencies import ApiServices, CurrentUser, RequestID
from ..mutations import mutate


def queue_router(services: ApiServices) -> APIRouter:
    router = APIRouter()

    @router.post("/api/queue")
    async def add(
        user: CurrentUser,
        body: dto.AddInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Add(
                body.source_url,
                user.account.profile,
            ),
            response,
            active,
        )

    @router.post("/api/queue/batch")
    async def add_many(
        user: CurrentUser,
        body: dto.BatchInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.AddMany(
                body.source_urls,
                user.account.profile,
                body.skip_duplicates,
            ),
            response,
            active,
        )

    @router.delete("/api/queue/{entry_id}")
    async def remove(
        user: CurrentUser,
        entry_id: UUID,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Remove(entry_id), response, active
        )

    @router.post("/api/queue/{entry_id}/move")
    async def move(
        user: CurrentUser,
        entry_id: UUID,
        body: dto.MoveInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Move(entry_id, body.before_entry_id, body.expected_queue_revision),
            response,
            active,
        )

    @router.post("/api/queue/clear")
    async def clear(
        user: CurrentUser,
        body: dto.ClearInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user,
            request_id,
            commands.Clear(body.expected_queue_revision, body.contributor_id),
            response,
            active,
        )

    @router.post("/api/queue/undo")
    async def undo(
        user: CurrentUser,
        body: dto.UndoInput,
        response: Response,
        request_id: RequestID,
        active: Annotated[Session, Depends(services.player)],
    ) -> dto.MutationResult:
        return await mutate(
            user, request_id, commands.Undo(body.undo_id), response, active
        )

    return router

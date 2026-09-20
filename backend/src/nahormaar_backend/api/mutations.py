# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Serialize a committed command result for the requesting browser."""

from uuid import UUID

from fastapi import Response

from ..application.playback import PlaybackController
from ..domain import commands
from . import schemas as dto
from .dependencies import CurrentUser


async def mutate(
    user: CurrentUser,
    request_id: UUID,
    command: commands.Command,
    response: Response,
    active: PlaybackController,
) -> dto.MutationResult:
    reply = await active.request(
        request_id,
        command,
        actor_id=user.account.profile.id,
        actor=user.account.profile,
    )
    response.status_code = reply.outcome.status_code
    return dto.MutationResult(
        request_id=request_id,
        code=reply.outcome.code,
        entry_id=reply.outcome.entry_id,
        replayed=reply.replayed,
        snapshot=dto.State.from_status(reply.status),
        added_count=reply.outcome.added_count,
        skipped_count=reply.outcome.skipped_count,
        removed_count=reply.outcome.removed_count,
        restored_count=reply.outcome.restored_count,
        undo_id=reply.outcome.undo_id,
        undo_expires_at=reply.outcome.undo_expires_at,
        actor=(
            dto.ContributorData.model_validate(reply.outcome.actor)
            if reply.outcome.actor is not None
            else None
        ),
        entries=tuple(dto.Entry.from_entry(entry) for entry in reply.outcome.entries),
    )

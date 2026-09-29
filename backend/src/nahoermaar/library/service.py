# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated use cases for personal track reactions."""

from collections.abc import Callable
from datetime import UTC, datetime

from nahoermaar.catalog.domain import TrackId
from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.users.domain import UserId

from .domain import LibraryError, LibraryErrorCode, ReactionValue, TrackReaction
from .read_model import (
    LibraryReadModel,
    LibrarySnapshot,
    LibraryTrackPage,
    ReactionParticipantPage,
    ReactionSummary,
)
from .repository import TrackReactionRepository

type Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LibraryService:
    """Own personal reaction writes and Library read contracts."""

    __slots__ = ("_catalog", "_clock", "_reader", "_units")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        catalog: CatalogService,
        reader: LibraryReadModel,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._catalog = catalog
        self._reader = reader
        self._clock = clock

    async def set_reaction(
        self,
        user_id: UserId,
        track_id: TrackId,
        value: ReactionValue,
    ) -> TrackReaction:
        await self._require_track(track_id)
        async with self._units() as work:
            reaction = await TrackReactionRepository(work.session).set(
                user_id, track_id, value, self._clock()
            )
            await work.commit()
        return reaction

    async def remove_reaction(self, user_id: UserId, track_id: TrackId) -> bool:
        async with self._units() as work:
            removed = await TrackReactionRepository(work.session).remove(
                user_id, track_id
            )
            await work.commit()
        return removed

    async def summaries(
        self,
        user_id: UserId,
        track_ids: tuple[TrackId, ...],
    ) -> tuple[ReactionSummary, ...]:
        if not 1 <= len(track_ids) <= 100:
            raise ValueError("Reaction summaries require between 1 and 100 tracks.")
        return await self._reader.summaries(user_id, track_ids)

    async def tracks(
        self,
        user_id: UserId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        query: str | None = None,
        snapshot: LibrarySnapshot | None = None,
    ) -> LibraryTrackPage:
        return await self._reader.tracks(
            user_id,
            reaction=reaction,
            page=page,
            page_size=page_size,
            query=query,
            snapshot=snapshot,
        )

    async def participants(
        self,
        track_id: TrackId,
        *,
        reaction: ReactionValue,
        page: int,
        page_size: int,
        snapshot: LibrarySnapshot | None = None,
    ) -> ReactionParticipantPage:
        await self._require_track(track_id)
        return await self._reader.participants(
            track_id,
            reaction=reaction,
            page=page,
            page_size=page_size,
            snapshot=snapshot,
        )

    async def _require_track(self, track_id: TrackId) -> None:
        if track_id not in await self._catalog.tracks({track_id}):
            raise LibraryError(LibraryErrorCode.TRACK_NOT_FOUND, 404)

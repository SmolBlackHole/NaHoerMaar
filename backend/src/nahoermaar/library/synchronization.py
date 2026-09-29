# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Linked-playlist reconciliation shared by scheduled and manual refreshes."""

from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from nahoermaar.catalog.service import CatalogError, CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.observability import error_code
from nahoermaar.operations.jobs import (
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
)

from .domain import MAX_PLAYLIST_ENTRIES, Playlist, PlaylistTrackSelection
from .repository import PlaylistRepository

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlaylistSyncResult:
    changed: int
    failed: int
    detail: JobRunDetail


class PlaylistSynchronizer:
    """Refresh one linked playlist while preserving its last good contents."""

    __slots__ = ("_catalog", "_units")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        catalog: CatalogService,
    ) -> None:
        self._units = units
        self._catalog = catalog

    async def synchronize(
        self,
        candidate: Playlist,
        *,
        now: datetime | None = None,
    ) -> PlaylistSyncResult:
        attempted_at = (now or datetime.now(UTC)).astimezone(UTC)
        source = candidate.source
        if source is None:
            return PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        try:
            materialized = await self._catalog.materialize_playlist(
                source.canonical_url,
                provider_key=source.provider_key,
                max_entries=MAX_PLAYLIST_ENTRIES,
            )
        except CatalogError as error:
            return await self._failed_result(
                candidate,
                error_code(error),
                attempted_at,
            )

        if (
            materialized.provider_key != source.provider_key
            or materialized.external_id != source.external_id
        ):
            return await self._failed_result(
                candidate,
                "playlist_source_identity_mismatch",
                attempted_at,
            )

        selections = tuple(
            PlaylistTrackSelection(entry.track.id, entry.source.id)
            for entry in materialized.entries
        )
        async with self._units() as work:
            changes = await PlaylistRepository(work.session).synchronize_linked(
                candidate.id,
                provider_key=source.provider_key,
                external_id=source.external_id,
                canonical_url=materialized.canonical_url,
                selections=selections,
                unavailable_entry_count=materialized.unavailable_entry_count,
                truncated=materialized.truncated,
                now=attempted_at,
            )
            await work.commit()
        if changes is None:
            return PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        affected = changes.added + changes.removed + changes.moved
        detail = JobRunDetail(
            kind=JobRunDetailKind.PLAYLIST_SYNC,
            outcome=(
                JobRunDetailOutcome.CHANGED
                if changes.content_changed
                else JobRunDetailOutcome.UNCHANGED
            ),
            label=candidate.name,
            summary=(
                f"{changes.added} added, {changes.removed} removed, "
                f"{changes.moved} moved, "
                f"{materialized.unavailable_entry_count} unavailable, "
                f"{changes.unchanged} unchanged."
            ),
            affected_count=affected,
            subject_id=str(candidate.id),
            source=source.provider_key,
        )
        return PlaylistSyncResult(int(changes.content_changed), 0, detail)

    async def _failed_result(
        self,
        candidate: Playlist,
        code: str,
        now: datetime,
    ) -> PlaylistSyncResult:
        source = candidate.source
        if source is None:
            return PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        async with self._units() as work:
            recorded = await PlaylistRepository(work.session).mark_linked_sync_failed(
                candidate.id,
                provider_key=source.provider_key,
                external_id=source.external_id,
                error_code=code,
                now=now,
            )
            await work.commit()
        if not recorded:
            return PlaylistSyncResult(0, 0, self._skipped_detail(candidate))
        _LOGGER.warning(
            "library.playlist_sync_failed playlist_id=%s provider=%s error=%s",
            candidate.id,
            source.provider_key,
            code,
        )
        return PlaylistSyncResult(
            0,
            1,
            JobRunDetail(
                kind=JobRunDetailKind.PLAYLIST_SYNC,
                outcome=JobRunDetailOutcome.FAILED,
                label=candidate.name,
                summary="Synchronization failed; cached tracks were kept.",
                subject_id=str(candidate.id),
                source=source.provider_key,
                error_code=code,
            ),
        )

    @staticmethod
    def _skipped_detail(candidate: Playlist) -> JobRunDetail:
        return JobRunDetail(
            kind=JobRunDetailKind.PLAYLIST_SYNC,
            outcome=JobRunDetailOutcome.SKIPPED,
            label=candidate.name,
            summary="The playlist source changed before synchronization completed.",
            subject_id=str(candidate.id),
        )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded Catalog maintenance owned by the Catalog module."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from nahoermaar.database.uow import UnitOfWorkFactory
from nahoermaar.observability import error_code, safe_log_value
from nahoermaar.operations.jobs import (
    JobId,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
)
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    cleanup_detail,
)
from nahoermaar.operations.scheduler import (
    BooleanJobControl,
    IntegerJobControl,
    JobControls,
    JobDefinition,
    JobDescriptor,
    JobExecution,
    JobProgressUnit,
    JobResult,
)
from nahoermaar.views.catalog import CatalogCleanupCandidates, CatalogCleanupReader

from .domain import SourceAvailability, TrackSource
from .repository import (
    CatalogRepository,
    DiscoveryRefreshCandidate,
    DiscoveryRepository,
)
from .service import (
    CatalogService,
    added_metadata_fields,
    missing_detail_fields,
    needs_detail,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ItemResult:
    detail: JobRunDetail
    changed: bool = False
    failed: bool = False


class CatalogMaintenance:
    """Repair incomplete metadata and refresh recently used discovery data."""

    __slots__ = (
        "_clock",
        "_delay",
        "_failures",
        "_interval",
        "_parallel_requests",
        "_service",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        service: CatalogService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(minutes=5),
        delay: float = 1.0,
        parallel_requests: int = 4,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("Catalog maintenance interval must be positive.")
        if delay < 0:
            raise ValueError("Catalog maintenance delay must not be negative.")
        if not 1 <= parallel_requests <= 10:
            raise ValueError(
                "Catalog maintenance parallel requests must be between 1 and 10."
            )
        self._units = units
        self._service = service
        self._clock = clock
        self._interval = interval
        self._delay = delay
        self._parallel_requests = parallel_requests
        self._failures: dict[str, tuple[int, datetime]] = {}

    def definition(self) -> JobDefinition:
        return JobDefinition(
            JobDescriptor(
                id=JobId.CATALOG_MAINTENANCE,
                module="catalog",
                label="Catalog maintenance",
                description=(
                    "Repairs incomplete track metadata and refreshes stale search "
                    "results."
                ),
                scope=("track metadata", "discovery cache"),
                interval=self._interval,
                controls=JobControls(IntegerJobControl(10, 1, 100)),
                parallel_requests=self._parallel_requests,
            ),
            self.run,
            run_on_startup=True,
        )

    async def run(self, execution: JobExecution) -> JobResult:
        batch_size = execution.options.batch_size
        now = self._clock()
        sources, discoveries = await self._candidates(now, batch_size)
        candidates = len(sources) + len(discoveries)
        processed = 0
        execution.report_progress(0, candidates, JobProgressUnit.RECORDS)
        semaphore = asyncio.Semaphore(self._parallel_requests)

        async def repair(source: TrackSource) -> _ItemResult:
            nonlocal processed
            try:
                return await self._repair_source(source, semaphore)
            finally:
                processed += 1
                execution.report_progress(
                    processed,
                    candidates,
                    JobProgressUnit.RECORDS,
                )

        async def refresh(candidate: DiscoveryRefreshCandidate) -> _ItemResult:
            nonlocal processed
            try:
                return await self._refresh_discovery(candidate, now, semaphore)
            finally:
                processed += 1
                execution.report_progress(
                    processed,
                    candidates,
                    JobProgressUnit.RECORDS,
                )

        results = await asyncio.gather(
            *(repair(source) for source in sources),
            *(refresh(candidate) for candidate in discoveries),
        )
        changed = sum(result.changed for result in results)
        failures = sum(result.failed for result in results)
        _LOGGER.info(
            "catalog.maintenance_completed trigger=%s batch=%d "
            "metadata_candidates=%d discovery_candidates=%d changed=%d failures=%d",
            execution.trigger,
            batch_size,
            len(sources),
            len(discoveries),
            changed,
            failures,
        )
        return JobResult(
            candidate_count=candidates,
            processed_count=processed,
            changed_count=changed,
            failure_count=failures,
            error_code="catalog_maintenance_partial" if failures else None,
            details=tuple(result.detail for result in results),
        )

    async def _candidates(
        self,
        now: datetime,
        batch_size: int,
    ) -> tuple[tuple[TrackSource, ...], tuple[DiscoveryRefreshCandidate, ...]]:
        async with self._units() as work:
            sources = await CatalogRepository(work.session).incomplete_sources(
                checked_before=now - self._interval,
                limit=batch_size,
            )
            discoveries = await DiscoveryRepository(work.session).refresh_candidates(
                now=now,
                requested_after=now - timedelta(days=7),
                limit=max(0, batch_size - len(sources)),
            )
        return sources, discoveries

    async def _repair_source(
        self,
        source: TrackSource,
        semaphore: asyncio.Semaphore,
    ) -> _ItemResult:
        async with semaphore:
            try:
                track = await self._service.track(
                    source.source_url,
                    provider_key=source.provider.value,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = error_code(error)
                _LOGGER.warning(
                    "catalog.maintenance_metadata_failed track_id=%s title=%r "
                    "source_id=%s provider=%s error=%s",
                    source.track_id,
                    safe_log_value(source.observed_title),
                    source.id,
                    source.provider,
                    code,
                )
                return _ItemResult(
                    JobRunDetail(
                        kind=JobRunDetailKind.TRACK_METADATA,
                        outcome=JobRunDetailOutcome.FAILED,
                        label=source.observed_title,
                        summary="The provider could not resolve this track.",
                        subject_id=str(source.track_id),
                        source=source.provider.value,
                        error_code=code,
                    ),
                    failed=True,
                )
            finally:
                await self._pause()

        stored_source = next(
            (candidate for candidate in track.sources if candidate.id == source.id),
            None,
        )
        if stored_source is None:
            _LOGGER.warning(
                "catalog.maintenance_metadata_incomplete track_id=%s title=%r "
                "source_id=%s provider=%s reason=source_missing_after_refresh",
                track.id,
                safe_log_value(track.title),
                source.id,
                source.provider,
            )
            return _ItemResult(
                JobRunDetail(
                    kind=JobRunDetailKind.TRACK_METADATA,
                    outcome=JobRunDetailOutcome.UNCHANGED,
                    label=track.title,
                    summary="The refreshed track no longer contained the selected source.",
                    subject_id=str(track.id),
                    source=source.provider.value,
                )
            )
        if needs_detail(track, stored_source):
            missing = missing_detail_fields(track, stored_source)
            _LOGGER.info(
                "catalog.maintenance_metadata_incomplete track_id=%s title=%r "
                "source_id=%s provider=%s remaining_fields=%s",
                track.id,
                safe_log_value(track.title),
                source.id,
                source.provider,
                ",".join(missing),
            )
            return _ItemResult(
                JobRunDetail(
                    kind=JobRunDetailKind.TRACK_METADATA,
                    outcome=JobRunDetailOutcome.UNCHANGED,
                    label=track.title,
                    summary=f"Still missing {', '.join(missing)}.",
                    subject_id=str(track.id),
                    source=source.provider.value,
                )
            )

        added = added_metadata_fields(source, stored_source)
        _LOGGER.info(
            "catalog.maintenance_metadata_repaired track_id=%s title=%r "
            "source_id=%s provider=%s added_fields=%s resolution=%s",
            track.id,
            safe_log_value(track.title),
            source.id,
            source.provider,
            ",".join(added) or "none",
            "metadata_added" if added else "detail_confirmed",
        )
        return _ItemResult(
            JobRunDetail(
                kind=JobRunDetailKind.TRACK_METADATA,
                outcome=JobRunDetailOutcome.CHANGED,
                label=track.title,
                summary=(
                    f"Added {', '.join(added)}."
                    if added
                    else "Confirmed complete metadata."
                ),
                affected_count=1,
                subject_id=str(track.id),
                source=source.provider.value,
            ),
            changed=True,
        )

    async def _refresh_discovery(
        self,
        candidate: DiscoveryRefreshCandidate,
        now: datetime,
        semaphore: asyncio.Semaphore,
    ) -> _ItemResult:
        retry_key = (
            f"discovery:{candidate.kind.value}:{candidate.provider_key}:"
            f"{candidate.locator}:{candidate.limit}"
        )
        if not self._ready(retry_key, now):
            return _ItemResult(
                JobRunDetail(
                    kind=JobRunDetailKind.DISCOVERY_REFRESH,
                    outcome=JobRunDetailOutcome.SKIPPED,
                    label=candidate.locator,
                    summary="Waiting for the provider retry window.",
                    subject_id=candidate.locator,
                    source=candidate.provider_key,
                )
            )
        async with semaphore:
            try:
                snapshot = await self._service.refresh_candidate(candidate)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failed(retry_key, now)
                code = error_code(error)
                _LOGGER.warning(
                    "catalog.maintenance_discovery_failed kind=%s provider=%s "
                    "locator=%r error=%s",
                    candidate.kind,
                    candidate.provider_key,
                    safe_log_value(candidate.locator),
                    code,
                )
                return _ItemResult(
                    JobRunDetail(
                        kind=JobRunDetailKind.DISCOVERY_REFRESH,
                        outcome=JobRunDetailOutcome.FAILED,
                        label=candidate.locator,
                        summary=f"Could not refresh this {candidate.kind.value}.",
                        subject_id=candidate.locator,
                        source=candidate.provider_key,
                        error_code=code,
                    ),
                    failed=True,
                )
            finally:
                await self._pause()

        self._failures.pop(retry_key, None)
        _LOGGER.info(
            "catalog.maintenance_discovery_refreshed kind=%s provider=%s "
            "locator=%r snapshot_id=%s entries=%d",
            candidate.kind,
            candidate.provider_key,
            safe_log_value(candidate.locator),
            snapshot.id,
            len(snapshot.entries),
        )
        return _ItemResult(
            JobRunDetail(
                kind=JobRunDetailKind.DISCOVERY_REFRESH,
                outcome=JobRunDetailOutcome.CHANGED,
                label=candidate.locator,
                summary=(
                    f"Refreshed {candidate.kind.value} with "
                    f"{len(snapshot.entries)} entries."
                ),
                affected_count=len(snapshot.entries),
                subject_id=str(snapshot.id),
                source=candidate.provider_key,
            ),
            changed=True,
        )

    def _ready(self, key: str, now: datetime) -> bool:
        failure = self._failures.get(key)
        return failure is None or failure[1] <= now

    def _failed(self, key: str, now: datetime) -> None:
        attempts = self._failures.get(key, (0, now))[0] + 1
        base_delay = max(60, int(self._interval.total_seconds()) * 2)
        delay = min(3600, base_delay * 2 ** (attempts - 1))
        self._failures[key] = (attempts, now + timedelta(seconds=delay))

    async def _pause(self) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)


class CatalogHousekeeping:
    """Remove expired discovery snapshots owned by Catalog."""

    __slots__ = ("_units",)

    def __init__(self, units: UnitOfWorkFactory) -> None:
        self._units = units

    def contribution(self) -> HousekeepingContribution:
        return HousekeepingContribution(
            "catalog",
            ("Discovery snapshots",),
            self.run,
        )

    async def run(
        self,
        context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        async with self._units() as work:
            removed = await DiscoveryRepository(work.session).prune_expired(
                context.now,
                limit=context.batch_size,
            )
            await work.commit()
        return (cleanup_detail("catalog", "Discovery snapshots", removed),)


class CatalogCleanup:
    """Remove old Catalog rows only after the reference view proves them unused."""

    __slots__ = ("_clock", "_interval", "_units", "_view")

    def __init__(
        self,
        units: UnitOfWorkFactory,
        view: CatalogCleanupReader,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(days=1),
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("Catalog cleanup interval must be positive.")
        self._units = units
        self._view = view
        self._clock = clock
        self._interval = interval

    def definition(self) -> JobDefinition:
        return JobDefinition(
            JobDescriptor(
                id=JobId.CATALOG_CLEANUP,
                module="catalog",
                label="Catalog cleanup",
                description=(
                    "Removes old provider sources and the tracks or artists left "
                    "unused after them."
                ),
                scope=("provider sources", "orphaned tracks", "orphaned artists"),
                interval=self._interval,
                controls=JobControls(
                    IntegerJobControl(100, 1, 1000),
                    preview=BooleanJobControl(False),
                    age_days=IntegerJobControl(90, 7, 3650),
                ),
            ),
            self.run,
            run_on_startup=False,
        )

    async def run(self, execution: JobExecution) -> JobResult:
        age_days = execution.options.age_days
        if age_days is None:
            raise RuntimeError("Catalog cleanup requires an age boundary.")
        checked_before = self._clock().astimezone(UTC) - timedelta(days=age_days)
        candidates = CatalogCleanupCandidates((), (), ())
        try:
            async with self._units() as work:
                candidates = await self._view.candidates(
                    work.session,
                    checked_before=checked_before,
                    limit=execution.options.batch_size,
                )
                source_count = len(candidates.sources)
                execution.report_progress(0, source_count, JobProgressUnit.RECORDS)
                if execution.options.preview:
                    details = self._details(candidates, preview=True)
                    execution.report_progress(
                        source_count,
                        source_count,
                        JobProgressUnit.RECORDS,
                    )
                    return JobResult(
                        candidate_count=source_count,
                        processed_count=source_count,
                        changed_count=0,
                        details=details,
                    )

                removed = await CatalogRepository(work.session).delete_orphans(
                    source_ids=tuple(item.id for item in candidates.sources),
                    track_ids=tuple(item.id for item in candidates.tracks),
                    artist_ids=tuple(item.id for item in candidates.artists),
                )
                await work.commit()
        except IntegrityError:
            _LOGGER.warning(
                "catalog.cleanup_reference_conflict candidates=%d cutoff=%s",
                len(candidates.sources),
                checked_before.isoformat(),
            )
            return JobResult(
                candidate_count=len(candidates.sources),
                processed_count=0,
                changed_count=0,
                failure_count=1,
                error_code="catalog_cleanup_reference_conflict",
                details=(
                    JobRunDetail(
                        kind=JobRunDetailKind.DATA_CLEANUP,
                        outcome=JobRunDetailOutcome.FAILED,
                        label="Catalog cleanup",
                        summary=(
                            "A candidate gained a durable reference while cleanup "
                            "was running. Nothing was removed."
                        ),
                        error_code="catalog_cleanup_reference_conflict",
                    ),
                ),
            )

        source_count = len(candidates.sources)
        execution.report_progress(source_count, source_count, JobProgressUnit.RECORDS)
        return JobResult(
            candidate_count=source_count,
            processed_count=source_count,
            changed_count=sum(removed),
            details=self._details(candidates, preview=False),
        )

    @staticmethod
    def _details(
        candidates: CatalogCleanupCandidates,
        *,
        preview: bool,
    ) -> tuple[JobRunDetail, ...]:
        outcome = (
            JobRunDetailOutcome.SKIPPED if preview else JobRunDetailOutcome.CHANGED
        )
        prefix = "Would remove" if preview else "Removed"
        details = [
            JobRunDetail(
                kind=JobRunDetailKind.DATA_CLEANUP,
                outcome=outcome,
                label=source.title,
                summary=(
                    f"{prefix} the unreferenced {source.provider.value} source "
                    f"{source.external_id}."
                ),
                affected_count=1,
                subject_id=str(source.id),
                source=source.provider.value,
            )
            for source in candidates.sources
        ]
        details.extend(
            JobRunDetail(
                kind=JobRunDetailKind.DATA_CLEANUP,
                outcome=outcome,
                label=track.title,
                summary=f"{prefix} the track left without a provider source.",
                affected_count=1,
                subject_id=str(track.id),
                source="track",
            )
            for track in candidates.tracks
        )
        details.extend(
            JobRunDetail(
                kind=JobRunDetailKind.DATA_CLEANUP,
                outcome=outcome,
                label=artist.name,
                summary=f"{prefix} the artist left without a catalog relation.",
                affected_count=1,
                subject_id=str(artist.id),
                source="artist",
            )
            for artist in candidates.artists
        )
        return tuple(details)


class SourceRevalidation:
    """Retry unavailable and repeatedly failing provider sources with backoff."""

    __slots__ = (
        "_clock",
        "_delay",
        "_interval",
        "_parallel_requests",
        "_service",
        "_units",
    )

    def __init__(
        self,
        units: UnitOfWorkFactory,
        service: CatalogService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(hours=1),
        delay: float = 1.0,
        parallel_requests: int = 4,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("Source revalidation interval must be positive.")
        if delay < 0:
            raise ValueError("Source revalidation delay must not be negative.")
        if not 1 <= parallel_requests <= 10:
            raise ValueError(
                "Source revalidation parallel requests must be between 1 and 10."
            )
        self._units = units
        self._service = service
        self._clock = clock
        self._interval = interval
        self._delay = delay
        self._parallel_requests = parallel_requests

    def definition(self) -> JobDefinition:
        return JobDefinition(
            JobDescriptor(
                id=JobId.SOURCE_REVALIDATION,
                module="catalog",
                label="Source revalidation",
                description=(
                    "Retries unavailable or failing provider sources after their "
                    "backoff window."
                ),
                scope=("unavailable sources", "provider failures"),
                interval=self._interval,
                controls=JobControls(IntegerJobControl(10, 1, 100)),
                parallel_requests=self._parallel_requests,
            ),
            self.run,
            run_on_startup=False,
        )

    async def run(self, execution: JobExecution) -> JobResult:
        now = self._clock()
        async with self._units() as work:
            sources = await CatalogRepository(work.session).revalidation_candidates(
                now=now,
                limit=execution.options.batch_size,
            )

        total = len(sources)
        processed = 0
        execution.report_progress(0, total, JobProgressUnit.RECORDS)
        semaphore = asyncio.Semaphore(self._parallel_requests)

        async def revalidate(source: TrackSource) -> _ItemResult:
            nonlocal processed
            try:
                return await self._revalidate(source, semaphore)
            finally:
                processed += 1
                execution.report_progress(processed, total, JobProgressUnit.RECORDS)

        results = await asyncio.gather(*(revalidate(source) for source in sources))
        failures = sum(result.failed for result in results)
        changed = sum(result.changed for result in results)
        _LOGGER.info(
            "catalog.source_revalidation_completed trigger=%s batch=%d "
            "candidates=%d restored=%d failures=%d",
            execution.trigger,
            execution.options.batch_size,
            total,
            changed,
            failures,
        )
        return JobResult(
            candidate_count=total,
            processed_count=processed,
            changed_count=changed,
            failure_count=failures,
            error_code="source_revalidation_partial" if failures else None,
            details=tuple(result.detail for result in results),
        )

    async def _revalidate(
        self,
        source: TrackSource,
        semaphore: asyncio.Semaphore,
    ) -> _ItemResult:
        async with semaphore:
            try:
                track = await self._service.track(
                    source.source_url,
                    provider_key=source.provider.value,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = error_code(error)
                async with self._units() as work:
                    failed_source = await CatalogRepository(work.session).source(
                        source.id
                    )
                retry_at = (
                    failed_source.retry_at
                    if failed_source is not None
                    else source.retry_at
                )
                retry_summary = (
                    f" Next retry after {retry_at.isoformat()}."
                    if retry_at is not None
                    else ""
                )
                _LOGGER.warning(
                    "catalog.source_revalidation_failed source_id=%s provider=%s "
                    "external_id=%s retry_at=%s error=%s",
                    source.id,
                    source.provider,
                    safe_log_value(source.external_id),
                    retry_at.isoformat() if retry_at is not None else None,
                    code,
                )
                return _ItemResult(
                    JobRunDetail(
                        kind=JobRunDetailKind.SOURCE_REVALIDATION,
                        outcome=JobRunDetailOutcome.FAILED,
                        label=source.observed_title,
                        summary=(
                            f"The provider source is still unavailable.{retry_summary}"
                        ),
                        subject_id=str(source.id),
                        source=source.provider.value,
                        error_code=code,
                    ),
                    failed=True,
                )
            finally:
                if self._delay:
                    await asyncio.sleep(self._delay)

        stored = next(
            (candidate for candidate in track.sources if candidate.id == source.id),
            None,
        )
        restored = (
            stored is not None
            and stored.availability is SourceAvailability.AVAILABLE
            and stored.failure_count == 0
        )
        if not restored:
            return _ItemResult(
                JobRunDetail(
                    kind=JobRunDetailKind.SOURCE_REVALIDATION,
                    outcome=JobRunDetailOutcome.FAILED,
                    label=source.observed_title,
                    summary="The provider response did not restore this source.",
                    subject_id=str(source.id),
                    source=source.provider.value,
                    error_code="source_not_restored",
                ),
                failed=True,
            )
        return _ItemResult(
            JobRunDetail(
                kind=JobRunDetailKind.SOURCE_REVALIDATION,
                outcome=JobRunDetailOutcome.CHANGED,
                label=track.title,
                summary="Restored the provider source and cleared its retry state.",
                affected_count=1,
                subject_id=str(source.id),
                source=source.provider.value,
            ),
            changed=True,
        )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded Catalog maintenance owned by the Catalog module."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.observability import error_code, safe_log_value
from nahoermaar.operations.jobs import (
    JobId,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
)
from nahoermaar.operations.scheduler import (
    IntegerJobControl,
    JobControls,
    JobDefinition,
    JobDescriptor,
    JobExecution,
    JobProgressUnit,
    JobResult,
)

from .domain import TrackSource
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
type UnitOfWorkFactory = Callable[[], UnitOfWork]


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
                return await self._repair_source(source, now, semaphore)
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
        now: datetime,
        semaphore: asyncio.Semaphore,
    ) -> _ItemResult:
        retry_key = f"source:{source.id}"
        if not self._ready(retry_key, now):
            return _ItemResult(
                JobRunDetail(
                    kind=JobRunDetailKind.TRACK_METADATA,
                    outcome=JobRunDetailOutcome.SKIPPED,
                    label=source.observed_title,
                    summary="Waiting for the provider retry window.",
                    subject_id=str(source.track_id),
                    source=source.provider.value,
                )
            )
        async with semaphore:
            try:
                track = await self._service.track(
                    source.source_url,
                    provider_key=source.provider.value,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failed(retry_key, now)
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

        self._failures.pop(retry_key, None)
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

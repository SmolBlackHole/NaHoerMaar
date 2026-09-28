# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Composite bounded housekeeping owned by the Operations module."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging

from nahoermaar.observability import error_code

from .jobs import (
    JobId,
    JobRunDetail,
    JobRunDetailKind,
    JobRunDetailOutcome,
)
from .scheduler import (
    IntegerJobControl,
    JobControls,
    JobDefinition,
    JobDescriptor,
    JobExecution,
    JobProgressUnit,
    JobResult,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HousekeepingContext:
    """Inputs shared by one bounded housekeeping run."""

    now: datetime
    batch_size: int


type HousekeepingRunner = Callable[
    [HousekeepingContext], Awaitable[tuple[JobRunDetail, ...]]
]


@dataclass(frozen=True, slots=True)
class HousekeepingContribution:
    """One module-owned group of housekeeping steps."""

    module: str
    labels: tuple[str, ...]
    run: HousekeepingRunner

    def __post_init__(self) -> None:
        if not self.module.strip():
            raise ValueError("Housekeeping contribution module must not be empty.")
        if not self.labels or any(not label.strip() for label in self.labels):
            raise ValueError("Housekeeping contribution labels must not be empty.")


def cleanup_detail(module: str, label: str, count: int) -> JobRunDetail:
    """Describe one successful module-owned cleanup step."""
    if count < 0:
        raise ValueError("Cleanup count must not be negative.")
    return JobRunDetail(
        kind=JobRunDetailKind.DATA_CLEANUP,
        outcome=(
            JobRunDetailOutcome.CHANGED if count else JobRunDetailOutcome.UNCHANGED
        ),
        label=label,
        summary=(
            f"Removed {count} expired record{'s' if count != 1 else ''}."
            if count
            else "No expired records found."
        ),
        affected_count=count,
        source=module,
    )


class HousekeepingMaintenance:
    """Run independent module contributions as one visible job."""

    __slots__ = ("_clock", "_contributions", "_interval")

    def __init__(
        self,
        contributions: tuple[HousekeepingContribution, ...],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        interval: timedelta = timedelta(hours=1),
    ) -> None:
        if not contributions:
            raise ValueError("Housekeeping requires at least one contribution.")
        if interval <= timedelta(0):
            raise ValueError("Housekeeping interval must be positive.")
        self._contributions = contributions
        self._clock = clock
        self._interval = interval

    def definition(self) -> JobDefinition:
        return JobDefinition(
            JobDescriptor(
                id=JobId.HOUSEKEEPING,
                module="operations",
                label="Data housekeeping",
                description=(
                    "Removes expired sessions, receipts, cache snapshots, incidents "
                    "and superseded avatars."
                ),
                scope=(
                    "Expires login attempts and browser sessions",
                    "Removes queue undo data and operation receipts",
                    "Drops expired search snapshots and incidents",
                    "Trims job history and superseded Discord avatars",
                ),
                interval=self._interval,
                controls=JobControls(IntegerJobControl(10_000, 1, 10_000)),
            ),
            self.run,
            run_on_startup=True,
        )

    async def run(self, execution: JobExecution) -> JobResult:
        context = HousekeepingContext(
            self._clock().astimezone(UTC),
            execution.options.batch_size,
        )
        total_steps = sum(
            len(contribution.labels) for contribution in self._contributions
        )
        completed_steps = 0
        details: list[JobRunDetail] = []
        execution.report_progress(0, total_steps, JobProgressUnit.STEPS)

        for contribution in self._contributions:
            try:
                current = await contribution.run(context)
                if tuple(detail.label for detail in current) != contribution.labels:
                    raise ValueError(
                        f"{contribution.module} returned unexpected cleanup details."
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = error_code(error)
                current = tuple(
                    JobRunDetail(
                        kind=JobRunDetailKind.DATA_CLEANUP,
                        outcome=JobRunDetailOutcome.FAILED,
                        label=label,
                        summary="Cleanup failed before this step could finish.",
                        source=contribution.module,
                        error_code=code,
                    )
                    for label in contribution.labels
                )
                _LOGGER.exception(
                    "jobs.housekeeping_contribution_failed module=%s error=%s",
                    contribution.module,
                    code,
                )
            details.extend(current)
            completed_steps += len(contribution.labels)
            execution.report_progress(
                completed_steps,
                total_steps,
                JobProgressUnit.STEPS,
            )

        changed = sum(detail.affected_count for detail in details)
        failures = sum(
            detail.outcome is JobRunDetailOutcome.FAILED for detail in details
        )
        _LOGGER.info(
            "jobs.housekeeping_completed trigger=%s batch=%d steps=%d changed=%d "
            "failures=%d",
            execution.trigger,
            context.batch_size,
            total_steps,
            changed,
            failures,
        )
        return JobResult(
            candidate_count=changed,
            processed_count=changed,
            changed_count=changed,
            failure_count=failures,
            error_code="housekeeping_partial" if failures else None,
            details=tuple(details),
        )

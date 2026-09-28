# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Composition boundary for read-only statistics projections."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.service import AccessService

from .service import StatisticsService

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StatisticsModule:
    """Public read boundary exposed by the Statistics module."""

    service: StatisticsService


def create_statistics_module(
    units: UnitFactory,
    timezone: str,
    access: AccessService,
    *,
    clock: Clock = _utc_now,
) -> StatisticsModule:
    """Construct Statistics from process dependencies and configuration."""
    return StatisticsModule(
        StatisticsService(
            units,
            ZoneInfo(timezone),
            access,
            clock=clock,
        )
    )

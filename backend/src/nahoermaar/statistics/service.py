# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Period handling and access policy for statistics read projections."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
import logging
from zoneinfo import ZoneInfo

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import AuthError, AuthErrorCode, UserId
from nahoermaar.users.repository import UserRepository

from .repository import (
    DailyActivity,
    RankedArtist,
    RankedListener,
    RankedTrack,
    StatisticsRepository,
    Totals,
)

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StatisticsPeriod(StrEnum):
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class Coverage:
    period: StatisticsPeriod
    timezone: str
    started_at: datetime
    ended_at: datetime
    recorded_since: datetime | None
    partial: bool


@dataclass(frozen=True, slots=True)
class StatisticsReport:
    user_id: UserId | None
    coverage: Coverage
    totals: Totals
    daily_activity: tuple[DailyActivity, ...]
    top_tracks: tuple[RankedTrack, ...]
    top_artists: tuple[RankedArtist, ...]
    top_listeners: tuple[RankedListener, ...]

    @property
    def completion_rate(self) -> float | None:
        ended = (
            self.totals.completed
            + self.totals.skipped
            + self.totals.stopped
            + self.totals.failed
        )
        return self.totals.completed / ended if ended else None

    @property
    def skip_rate(self) -> float | None:
        ended = (
            self.totals.completed
            + self.totals.skipped
            + self.totals.stopped
            + self.totals.failed
        )
        return self.totals.skipped / ended if ended else None


class StatisticsService:
    """Serve shared and personal statistics from one projection boundary."""

    __slots__ = ("_clock", "_timezone", "_units")

    def __init__(
        self,
        units: UnitFactory,
        timezone: ZoneInfo,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._timezone = timezone
        self._clock = clock

    async def overview(self, period: StatisticsPeriod) -> StatisticsReport:
        return await self._report(period, None)

    async def user(
        self,
        user_id: UserId,
        period: StatisticsPeriod,
    ) -> StatisticsReport:
        async with self._units() as work:
            user = await UserRepository(work.session).get(user_id)
        if user is None or not user.has_access:
            raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
        return await self._report(period, user_id)

    async def _report(
        self,
        period: StatisticsPeriod,
        user_id: UserId | None,
    ) -> StatisticsReport:
        ended_at = self._clock()
        if ended_at.utcoffset() is None:
            raise ValueError("Statistics clock must return a timezone-aware value.")
        ended_at = ended_at.astimezone(UTC)
        async with self._units() as work:
            repository = StatisticsRepository(work.session)
            recorded_since = await repository.recorded_since()
            started_at, partial = self._window(period, ended_at, recorded_since)
            totals = await repository.totals(
                started_at,
                ended_at,
                user_id=user_id,
            )
            daily_activity = await repository.daily_activity(
                started_at,
                ended_at,
                self._timezone.key,
                user_id=user_id,
            )
            top_tracks = await repository.top_tracks(
                started_at,
                ended_at,
                user_id=user_id,
            )
            top_artists = await repository.top_artists(
                started_at,
                ended_at,
                user_id=user_id,
            )
            top_listeners = (
                await repository.top_listeners(started_at, ended_at)
                if user_id is None
                else ()
            )
        _LOGGER.info(
            "statistics.projected scope=%s period=%s requests=%d plays=%d listening_seconds=%.3f partial=%s",
            user_id or "overview",
            period.value,
            totals.requests,
            totals.plays,
            totals.listening_seconds,
            partial,
        )
        return StatisticsReport(
            user_id,
            Coverage(
                period,
                self._timezone.key,
                started_at,
                ended_at,
                recorded_since,
                partial,
            ),
            totals,
            daily_activity,
            top_tracks,
            top_artists,
            top_listeners,
        )

    def _window(
        self,
        period: StatisticsPeriod,
        ended_at: datetime,
        recorded_since: datetime | None,
    ) -> tuple[datetime, bool]:
        if period is StatisticsPeriod.ALL:
            return recorded_since or ended_at, False
        days = 7 if period is StatisticsPeriod.DAYS_7 else 30
        local_end = ended_at.astimezone(self._timezone)
        first_day = local_end.date() - timedelta(days=days - 1)
        started_at = datetime.combine(
            first_day,
            time.min,
            tzinfo=self._timezone,
        ).astimezone(UTC)
        return started_at, recorded_since is None or recorded_since > started_at

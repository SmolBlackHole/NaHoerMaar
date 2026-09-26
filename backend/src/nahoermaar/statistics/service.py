# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Period handling and access policy for statistics read projections."""

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
import logging
from typing import overload
from zoneinfo import ZoneInfo

from nahoermaar.database.uow import UnitOfWork
from nahoermaar.users.domain import AuthError, AuthErrorCode, UserId
from nahoermaar.users.service import AccessService

from .models import (
    ActivityBucket,
    ActivityGranularity,
    Coverage,
    GroupStatisticsReport,
    PersonalStatisticsReport,
    StatisticsPeriod,
)
from .repository import PresenceInterval, StatisticsRepository

type Clock = Callable[[], datetime]
type UnitFactory = Callable[[], UnitOfWork]

_LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StatisticsService:
    """Serve shared and personal statistics from one projection boundary."""

    __slots__ = ("_access", "_clock", "_timezone", "_units")

    def __init__(
        self,
        units: UnitFactory,
        timezone: ZoneInfo,
        access: AccessService,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._units = units
        self._timezone = timezone
        self._access = access
        self._clock = clock

    async def overview(self, period: StatisticsPeriod) -> GroupStatisticsReport:
        return await self._report(period, None)

    async def user(
        self,
        user_id: UserId,
        period: StatisticsPeriod,
    ) -> PersonalStatisticsReport:
        if not await self._access.has_access(user_id):
            raise AuthError(AuthErrorCode.PROFILE_NOT_FOUND, 404)
        return await self._report(period, user_id)

    @overload
    async def _report(
        self,
        period: StatisticsPeriod,
        user_id: None,
    ) -> GroupStatisticsReport: ...

    @overload
    async def _report(
        self,
        period: StatisticsPeriod,
        user_id: UserId,
    ) -> PersonalStatisticsReport: ...

    async def _report(
        self,
        period: StatisticsPeriod,
        user_id: UserId | None,
    ) -> GroupStatisticsReport | PersonalStatisticsReport:
        ended_at = self._clock()
        if ended_at.utcoffset() is None:
            raise ValueError("Statistics clock must return a timezone-aware value.")
        ended_at = ended_at.astimezone(UTC)
        granularity = self._granularity(period)
        async with self._units() as work:
            repository = StatisticsRepository(work.session)
            recorded_since = await repository.recorded_since()
            started_at, partial = self._window(period, ended_at, recorded_since)
            totals = await repository.totals(
                started_at,
                ended_at,
                user_id=user_id,
            )
            activity = await repository.activity(
                started_at,
                ended_at,
                self._timezone.key,
                granularity,
                user_id=user_id,
            )
            presence = await repository.presence_intervals(
                started_at,
                ended_at,
                user_id=user_id,
            )
            activity = self._complete_activity(
                started_at,
                ended_at,
                granularity,
                activity,
                presence,
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
            active_listeners = (
                await repository.active_listener_count(started_at, ended_at)
                if user_id is None
                else 0
            )
        _LOGGER.info(
            "statistics.projected scope=%s period=%s requests=%d plays=%d "
            "playback_seconds=%.3f listening_seconds=%.3f "
            "presence_seconds=%.3f partial=%s",
            user_id or "overview",
            period.value,
            totals.requests.total,
            totals.playback.overall.started,
            totals.playback_seconds,
            totals.listening_seconds,
            totals.presence_seconds,
            partial,
        )
        coverage = Coverage(
            period,
            granularity,
            self._timezone.key,
            started_at,
            ended_at,
            recorded_since,
            partial,
        )
        if user_id is None:
            return GroupStatisticsReport(
                coverage=coverage,
                totals=totals,
                activity=activity,
                top_tracks=top_tracks,
                top_artists=top_artists,
                active_listeners=active_listeners,
                top_listeners=top_listeners,
            )
        return PersonalStatisticsReport(
            coverage=coverage,
            totals=totals,
            activity=activity,
            top_tracks=top_tracks,
            top_artists=top_artists,
            user_id=user_id,
        )

    def _window(
        self,
        period: StatisticsPeriod,
        ended_at: datetime,
        recorded_since: datetime | None,
    ) -> tuple[datetime, bool]:
        if period is StatisticsPeriod.ALL:
            return recorded_since or ended_at, False
        local_end = ended_at.astimezone(self._timezone)
        if period is StatisticsPeriod.YEAR:
            first_day = date(local_end.year, 1, 1)
        else:
            days = 7 if period is StatisticsPeriod.DAYS_7 else 30
            first_day = local_end.date() - timedelta(days=days - 1)
        started_at = datetime.combine(
            first_day,
            time.min,
            tzinfo=self._timezone,
        ).astimezone(UTC)
        return started_at, recorded_since is None or recorded_since > started_at

    @staticmethod
    def _granularity(period: StatisticsPeriod) -> ActivityGranularity:
        if period in (StatisticsPeriod.DAYS_7, StatisticsPeriod.DAYS_30):
            return ActivityGranularity.DAY
        return ActivityGranularity.MONTH

    def _complete_activity(
        self,
        started_at: datetime,
        ended_at: datetime,
        granularity: ActivityGranularity,
        activity: tuple[ActivityBucket, ...],
        presence: tuple[PresenceInterval, ...],
    ) -> tuple[ActivityBucket, ...]:
        recorded = {item.started_on: item for item in activity}
        completed: list[ActivityBucket] = []
        for started_on, bucket_start, bucket_end in self._bucket_ranges(
            started_at,
            ended_at,
            granularity,
        ):
            item = recorded.get(started_on)
            presence_seconds = sum(
                _overlap_seconds(
                    interval.started_at,
                    interval.ended_at,
                    bucket_start,
                    bucket_end,
                )
                for interval in presence
            )
            completed.append(
                ActivityBucket(
                    started_on=started_on,
                    granularity=granularity,
                    plays=item.plays if item else 0,
                    playback_seconds=item.playback_seconds if item else 0.0,
                    listening_seconds=item.listening_seconds if item else 0.0,
                    presence_seconds=presence_seconds,
                )
            )
        return tuple(completed)

    def _bucket_ranges(
        self,
        started_at: datetime,
        ended_at: datetime,
        granularity: ActivityGranularity,
    ) -> Iterable[tuple[date, datetime, datetime]]:
        if started_at >= ended_at:
            return
        local_start = started_at.astimezone(self._timezone)
        if granularity is ActivityGranularity.DAY:
            cursor = datetime.combine(
                local_start.date(),
                time.min,
                tzinfo=self._timezone,
            )
        else:
            cursor = datetime(
                local_start.year,
                local_start.month,
                1,
                tzinfo=self._timezone,
            )
        while cursor.astimezone(UTC) < ended_at:
            following = _next_local_bucket(cursor, granularity)
            bucket_start = max(cursor.astimezone(UTC), started_at)
            bucket_end = min(following.astimezone(UTC), ended_at)
            if bucket_start < bucket_end:
                yield cursor.date(), bucket_start, bucket_end
            cursor = following


def _next_local_bucket(
    current: datetime,
    granularity: ActivityGranularity,
) -> datetime:
    if granularity is ActivityGranularity.DAY:
        return datetime.combine(
            current.date() + timedelta(days=1),
            time.min,
            tzinfo=current.tzinfo,
        )
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    return datetime(year, month, 1, tzinfo=current.tzinfo)


def _overlap_seconds(
    interval_start: datetime,
    interval_end: datetime,
    bucket_start: datetime,
    bucket_end: datetime,
) -> float:
    start = max(interval_start, bucket_start)
    end = min(interval_end, bucket_end)
    return max(0.0, (end - start).total_seconds())

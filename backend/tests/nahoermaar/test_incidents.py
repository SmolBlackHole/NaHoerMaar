# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

from nahoermaar.database.core import Database
from nahoermaar.database.schema import migrate
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.operations.incidents import (
    IncidentKind,
    IncidentPeriod,
    IncidentService,
    IncidentSeverity,
    IncidentTrigger,
)
from nahoermaar.users.domain import (
    AccessRole,
    DiscordIdentity,
    User,
    UserId,
    UserProfile,
)
from nahoermaar.users.repository import UserRepository

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def test_incidents_are_deduplicated_summarized_and_expire() -> None:
    database = Database(os.environ["DATABASE_URL"])
    current = [NOW]

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    service = IncidentService(units, clock=lambda: current[0])
    user_id = UserId(uuid4())
    user_context = uuid4()

    async def scenario() -> None:
        await migrate(database.engine)
        async with units() as work:
            UserRepository(work.session).add(
                User(
                    user_id,
                    DiscordIdentity("9", "andrey", None, NOW),
                    NOW,
                    NOW,
                    profile=UserProfile("Andrey"),
                    role=AccessRole.OWNER,
                )
            )
            await work.commit()

        assert await service.record(
            severity=IncidentSeverity.WARNING,
            kind=IncidentKind.REJECTED,
            component="messaging",
            error_code="nothing_to_play",
            actor_id=user_id,
            operation_type="Play",
            correlation_id=user_context,
            trigger=IncidentTrigger.USER,
        )
        assert not await service.record(
            severity=IncidentSeverity.WARNING,
            kind=IncidentKind.REJECTED,
            component="http",
            error_code="nothing_to_play",
            actor_id=user_id,
            operation_type="POST /api/player/play",
            correlation_id=user_context,
            trigger=IncidentTrigger.USER,
        )
        await service.record(
            severity=IncidentSeverity.WARNING,
            kind=IncidentKind.RETRY,
            component="playback",
            error_code="AudioSourceNotReady",
            actor_id=None,
            operation_type="playback.start",
            correlation_id=uuid4(),
            trigger=IncidentTrigger.SYSTEM,
        )
        await service.record(
            severity=IncidentSeverity.WARNING,
            kind=IncidentKind.RECOVERED,
            component="playback",
            error_code="AudioSourceNotReady",
            actor_id=None,
            operation_type="playback.start",
            correlation_id=uuid4(),
            trigger=IncidentTrigger.SYSTEM,
        )
        await service.record(
            severity=IncidentSeverity.ERROR,
            kind=IncidentKind.FAILED,
            component="playback",
            error_code="VoiceConnectionError",
            actor_id=None,
            operation_type="voice.connect",
            correlation_id=uuid4(),
            trigger=IncidentTrigger.SYSTEM,
        )

        report = await service.report(IncidentPeriod.HOURS_24)
        assert report.totals.warnings == 3
        assert report.totals.errors == 1
        assert report.totals.user_triggered == 1
        assert report.totals.system_triggered == 3
        assert report.totals.rejected == 1
        assert report.totals.retries == 1
        assert report.totals.recoveries == 1
        assert report.associated_users[0].identity.display_name == "Andrey"
        assert report.associated_users[0].rejected_commands == 1
        assert report.common_errors[0].count == 2
        assert report.current_failure_free_seconds == 0
        assert len(report.recent) == 4

        current[0] = NOW + timedelta(days=15)
        expired = await service.report(IncidentPeriod.DAYS_14)
        assert expired.recorded_since is None
        assert expired.recent == ()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

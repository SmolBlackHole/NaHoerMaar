# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import insert

from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.statistics.models import ActivityGranularity, StatisticsPeriod
from nahoermaar.statistics.service import StatisticsService
from nahoermaar.users.domain import (
    AccessRole,
    AuthError,
    DiscordIdentity,
    User,
    UserId,
    UserProfile,
)
from nahoermaar.users.repository import UserRepository
from nahoermaar.users.service import AccessService, Operators
from nahoermaar.views.profile import ProfileView

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


async def _seed(database: Database) -> tuple[UserId, UserId]:
    owner_id = UserId(uuid4())
    listener_id = UserId(uuid4())
    blocked_id = UserId(uuid4())
    session_id = uuid4()
    artist_id = uuid4()
    first_track_id = uuid4()
    second_track_id = uuid4()
    first_source_id = uuid4()
    radio_run_id = uuid4()
    first_request_id = uuid4()
    second_request_id = uuid4()
    first_playback_id = uuid4()
    second_playback_id = uuid4()
    listener_presence_id = uuid4()
    owner_presence_id = uuid4()

    async with UnitOfWork(database.sessions) as work:
        users = UserRepository(work.session)
        users.add(
            User(
                owner_id,
                DiscordIdentity("100", "Owner", None, NOW),
                NOW,
                NOW,
                profile=UserProfile("Owner"),
                role=AccessRole.OWNER,
            )
        )
        users.add(
            User(
                listener_id,
                DiscordIdentity("200", "Listener", None, NOW),
                NOW,
                NOW,
                profile=UserProfile("Listener"),
                role=AccessRole.USER,
                access_granted_by=owner_id,
                access_granted_at=NOW,
            )
        )
        users.add(
            User(
                blocked_id,
                DiscordIdentity("300", "Blocked", None, NOW),
                NOW,
                NOW,
                profile=UserProfile("Blocked"),
            )
        )
        await work.session.flush()
        await work.session.execute(
            insert(Base.metadata.tables["artists"]).values(
                id=artist_id,
                name="Shared artist",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await work.session.execute(
            insert(Base.metadata.tables["tracks"]),
            (
                {
                    "id": first_track_id,
                    "title": "First track",
                    "duration_seconds": 180.0,
                    "artwork_url": "https://example.test/first.jpg",
                    "album_title": None,
                    "release_date": None,
                    "isrc": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": second_track_id,
                    "title": "Second track",
                    "duration_seconds": 180.0,
                    "artwork_url": None,
                    "album_title": None,
                    "release_date": None,
                    "isrc": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["track_artists"]),
            (
                {"track_id": first_track_id, "position": 0, "artist_id": artist_id},
                {"track_id": second_track_id, "position": 0, "artist_id": artist_id},
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["track_sources"]).values(
                id=first_source_id,
                track_id=first_track_id,
                provider="youtube_music",
                external_id="first-source",
                source_url="https://music.youtube.test/watch?v=first-source",
                observed_title="First track",
                observed_artist="Shared artist",
                observed_duration_seconds=180.0,
                observed_artwork_url="https://example.test/first.jpg",
                observed_album_title=None,
                observed_release_date=None,
                observed_isrc=None,
                uploader_name=None,
                uploader_url=None,
                quality="detail",
                availability="available",
                first_seen_at=NOW,
                checked_at=NOW,
            )
        )
        await work.session.execute(
            insert(Base.metadata.tables["listening_sessions"]).values(
                id=session_id,
                session_key="default",
                revision=0,
                queue_revision=0,
                channel_id=None,
                volume=1.0,
                crossfade_seconds=7,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await work.session.execute(
            insert(Base.metadata.tables["radio_runs"]).values(
                id=radio_run_id,
                session_id=session_id,
                seed_kind="track",
                seed_track_source_id=first_source_id,
                seed_discovery_snapshot_id=None,
                initiated_by=listener_id,
                started_at=NOW - timedelta(hours=2),
                ended_at=NOW,
                generation=uuid4(),
                state="active",
                continuation=None,
                request_id=None,
                error=None,
            )
        )
        await work.session.execute(
            insert(Base.metadata.tables["track_requests"]),
            (
                {
                    "id": first_request_id,
                    "session_id": session_id,
                    "track_id": first_track_id,
                    "source_id": None,
                    "requested_at": NOW - timedelta(hours=2),
                    "origin": "radio",
                    "requested_by": listener_id,
                    "radio_run_id": radio_run_id,
                },
                {
                    "id": second_request_id,
                    "session_id": session_id,
                    "track_id": second_track_id,
                    "source_id": None,
                    "requested_at": NOW - timedelta(hours=1),
                    "origin": "manual",
                    "requested_by": listener_id,
                    "radio_run_id": None,
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["playback_records"]),
            (
                {
                    "id": first_playback_id,
                    "session_id": session_id,
                    "request_id": first_request_id,
                    "started_at": NOW - timedelta(hours=2) + timedelta(seconds=60),
                    "audio_seconds": 120.0,
                    "group_audio_seconds": 120.0,
                    "ended_at": NOW - timedelta(hours=2) + timedelta(seconds=180),
                    "end_reason": "completed",
                },
                {
                    "id": second_playback_id,
                    "session_id": session_id,
                    "request_id": second_request_id,
                    "started_at": NOW - timedelta(hours=1) + timedelta(seconds=60),
                    "audio_seconds": 67.0,
                    "group_audio_seconds": 60.0,
                    "ended_at": NOW - timedelta(hours=1) + timedelta(seconds=127),
                    "end_reason": "skipped",
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["playback_listeners"]),
            (
                {
                    "playback_id": first_playback_id,
                    "user_id": listener_id,
                    "audio_seconds": 100.0,
                    "first_heard_at": NOW - timedelta(hours=2) + timedelta(seconds=60),
                    "last_heard_at": NOW - timedelta(hours=2) + timedelta(seconds=160),
                },
                {
                    "playback_id": second_playback_id,
                    "user_id": listener_id,
                    "audio_seconds": 40.0,
                    "first_heard_at": NOW - timedelta(hours=1) + timedelta(seconds=60),
                    "last_heard_at": NOW - timedelta(hours=1) + timedelta(seconds=100),
                },
                {
                    "playback_id": first_playback_id,
                    "user_id": owner_id,
                    "audio_seconds": 20.0,
                    "first_heard_at": NOW - timedelta(hours=2) + timedelta(seconds=60),
                    "last_heard_at": NOW - timedelta(hours=2) + timedelta(seconds=80),
                },
                {
                    "playback_id": second_playback_id,
                    "user_id": owner_id,
                    "audio_seconds": 10.0,
                    "first_heard_at": NOW - timedelta(hours=1) + timedelta(seconds=60),
                    "last_heard_at": NOW - timedelta(hours=1) + timedelta(seconds=70),
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["listener_presence"]),
            (
                {
                    "id": listener_presence_id,
                    "session_id": session_id,
                    "user_id": listener_id,
                    "joined_at": NOW - timedelta(minutes=90),
                    "confirmed_at": NOW - timedelta(minutes=5),
                    "deafened": False,
                    "left_at": NOW,
                },
                {
                    "id": owner_presence_id,
                    "session_id": session_id,
                    "user_id": owner_id,
                    "joined_at": NOW - timedelta(minutes=30),
                    "confirmed_at": NOW - timedelta(minutes=10),
                    "deafened": True,
                    "left_at": None,
                },
            ),
        )
        await work.commit()
    return listener_id, blocked_id


def test_statistics_project_shared_and_personal_facts_without_double_counting() -> None:
    database = _database()

    async def scenario() -> None:
        listener_id, blocked_id = await _seed(database)

        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        service = StatisticsService(
            units,
            ZoneInfo("Europe/Berlin"),
            AccessService(units, Operators("9", ())),
            clock=lambda: NOW,
        )
        profiles = ProfileView(units, service)
        overview = await service.overview(StatisticsPeriod.DAYS_7)
        personal = await service.user(listener_id, StatisticsPeriod.DAYS_7)

        assert overview.coverage.partial
        assert overview.coverage.timezone == "Europe/Berlin"
        assert overview.coverage.granularity is ActivityGranularity.DAY
        assert overview.totals.requests.total == 2
        assert overview.totals.requests.manual == 1
        assert overview.totals.requests.radio == 1
        assert overview.totals.playback.overall.started == 2
        assert overview.totals.playback.overall.completed == 1
        assert overview.totals.playback.overall.skipped == 1
        assert overview.totals.playback.manual.skipped == 1
        assert overview.totals.playback.radio.completed == 1
        assert overview.totals.listening_seconds == 170.0
        assert overview.totals.presence_seconds == 6600.0
        assert overview.totals.unique_tracks == 2
        assert overview.totals.unique_artists == 1
        assert overview.totals.average_wait_seconds == 60.0
        assert overview.totals.playback.overall.completion_rate == 0.5
        assert overview.totals.playback.overall.skip_rate == 0.5
        assert [track.title for track in overview.top_tracks] == [
            "First track",
            "Second track",
        ]
        assert overview.top_tracks[0].artist_names == ("Shared artist",)
        assert overview.top_artists[0].name == "Shared artist"
        assert overview.top_listeners[0].user_id == listener_id
        assert overview.top_listeners[0].discord_id == "200"
        assert overview.top_listeners[0].manual_requests == 1
        assert overview.top_listeners[0].presence_seconds == 5400.0
        assert len(overview.activity) == 7
        assert sum(day.listening_seconds for day in overview.activity) == 170.0
        assert sum(day.presence_seconds for day in overview.activity) == 6600.0

        assert personal.user_id == listener_id
        assert personal.totals.requests == overview.totals.requests
        assert personal.totals.playback == overview.totals.playback
        assert personal.totals.listening_seconds == 140.0
        assert personal.totals.presence_seconds == 5400.0
        assert personal.top_listeners == ()
        assert sum(day.listening_seconds for day in personal.activity) == 140.0

        yearly = await service.overview(StatisticsPeriod.YEAR)
        assert yearly.coverage.granularity is ActivityGranularity.MONTH
        assert len(yearly.activity) == 9
        assert yearly.activity[0].started_on.isoformat() == "2026-01-01"

        profile = await profiles.get(listener_id, StatisticsPeriod.DAYS_7)
        assert profile.identity.user_id == listener_id
        assert profile.identity.discord.username == "Listener"
        assert profile.identity.profile.display_name == "Listener"
        assert profile.identity.role is AccessRole.USER
        assert profile.statistics.totals == personal.totals
        assert profile.statistics.coverage == personal.coverage
        assert [track.title for track in profile.recent_tracks] == [
            "Second track",
            "First track",
        ]
        assert profile.recent_tracks[0].artist_names == ("Shared artist",)
        assert profile.recent_tracks[0].audio_seconds == 40.0
        assert profile.recent_tracks[1].audio_seconds == 100.0

        with pytest.raises(AuthError) as caught:
            await service.user(blocked_id, StatisticsPeriod.ALL)
        assert caught.value.status == 404
        with pytest.raises(AuthError) as caught:
            await profiles.get(blocked_id)
        assert caught.value.status == 404

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

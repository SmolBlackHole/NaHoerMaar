# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import insert

from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base, registered_table
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.statistics.main import create_statistics_module
from nahoermaar.statistics.models import (
    ActivityGranularity,
    ListenerAchievementFacts,
    ListenerBadgeKind,
    RankedListener,
    StatisticsPeriod,
)
from nahoermaar.statistics.service import (
    assign_listener_badges,
    calculate_active_day_streaks,
)
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


@dataclass(frozen=True, slots=True)
class _Seed:
    owner_id: UserId
    listener_id: UserId
    blocked_id: UserId
    first_track_id: UUID
    second_track_id: UUID


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


async def _seed(database: Database) -> _Seed:
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
    converted_request_id = uuid4()
    contagious_request_id = uuid4()
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
                {
                    "id": converted_request_id,
                    "session_id": session_id,
                    "track_id": first_track_id,
                    "source_id": None,
                    "requested_at": NOW - timedelta(minutes=30),
                    "origin": "manual",
                    "requested_by": owner_id,
                    "radio_run_id": None,
                },
                {
                    "id": contagious_request_id,
                    "session_id": session_id,
                    "track_id": second_track_id,
                    "source_id": None,
                    "requested_at": NOW - timedelta(minutes=20),
                    "origin": "manual",
                    "requested_by": owner_id,
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
    return _Seed(
        owner_id=owner_id,
        listener_id=listener_id,
        blocked_id=blocked_id,
        first_track_id=first_track_id,
        second_track_id=second_track_id,
    )


def test_statistics_project_shared_and_personal_facts_without_double_counting() -> None:
    database = _database()

    async def scenario() -> None:
        seeded = await _seed(database)
        listener_id = seeded.listener_id
        blocked_id = seeded.blocked_id

        def units() -> UnitOfWork:
            return UnitOfWork(database.sessions)

        module = create_statistics_module(
            units,
            "Europe/Berlin",
            AccessService(units, Operators("9", ())),
            clock=lambda: NOW,
        )
        service = module.service
        profiles = ProfileView(units, service)
        reactions = registered_table("track_reactions", consumer="Statistics tests")
        playlists = registered_table("playlists", consumer="Statistics tests")
        playlist_entries = registered_table(
            "playlist_entries", consumer="Statistics tests"
        )
        playlist_collaborators = registered_table(
            "playlist_collaborators", consumer="Statistics tests"
        )
        public_playlist_id = uuid4()
        collaborative_playlist_id = uuid4()
        async with units() as work:
            await work.session.execute(
                insert(reactions),
                [
                    {
                        "user_id": listener_id,
                        "track_id": seeded.first_track_id,
                        "value": "like",
                        "created_at": NOW - timedelta(minutes=2),
                        "updated_at": NOW - timedelta(minutes=2),
                    },
                    {
                        "user_id": listener_id,
                        "track_id": seeded.second_track_id,
                        "value": "dislike",
                        "created_at": NOW - timedelta(minutes=1),
                        "updated_at": NOW - timedelta(minutes=1),
                    },
                ],
            )
            await work.session.execute(
                insert(playlists),
                [
                    {
                        "id": public_playlist_id,
                        "owner_id": listener_id,
                        "owner_position": 0,
                        "name": "Public favourites",
                        "visibility": "public",
                        "source_provider_key": None,
                        "source_external_id": None,
                        "source_url": None,
                        "source_last_attempt_at": None,
                        "source_last_successful_sync_at": None,
                        "source_last_error_code": None,
                        "source_unavailable_entry_count": 0,
                        "source_truncated": False,
                        "revision": 0,
                        "created_at": NOW - timedelta(minutes=2),
                        "updated_at": NOW - timedelta(minutes=2),
                    },
                    {
                        "id": uuid4(),
                        "owner_id": listener_id,
                        "owner_position": 1,
                        "name": "Private favourites",
                        "visibility": "private",
                        "source_provider_key": None,
                        "source_external_id": None,
                        "source_url": None,
                        "source_last_attempt_at": None,
                        "source_last_successful_sync_at": None,
                        "source_last_error_code": None,
                        "source_unavailable_entry_count": 0,
                        "source_truncated": False,
                        "revision": 0,
                        "created_at": NOW - timedelta(minutes=1),
                        "updated_at": NOW - timedelta(minutes=1),
                    },
                    {
                        "id": collaborative_playlist_id,
                        "owner_id": seeded.owner_id,
                        "owner_position": 0,
                        "name": "Collaborative picks",
                        "visibility": "public",
                        "source_provider_key": None,
                        "source_external_id": None,
                        "source_url": None,
                        "source_last_attempt_at": None,
                        "source_last_successful_sync_at": None,
                        "source_last_error_code": None,
                        "source_unavailable_entry_count": 0,
                        "source_truncated": False,
                        "revision": 0,
                        "created_at": NOW - timedelta(minutes=1),
                        "updated_at": NOW - timedelta(seconds=30),
                    },
                ],
            )
            await work.session.execute(
                insert(playlist_collaborators).values(
                    playlist_id=collaborative_playlist_id,
                    user_id=listener_id,
                    granted_by=seeded.owner_id,
                    granted_at=NOW - timedelta(minutes=1),
                )
            )
            await work.session.execute(
                insert(playlist_entries).values(
                    id=uuid4(),
                    playlist_id=public_playlist_id,
                    track_id=seeded.first_track_id,
                    preferred_source_id=None,
                    added_by=listener_id,
                    position=0,
                    created_at=NOW - timedelta(minutes=1),
                )
            )
            await work.commit()
        overview = await service.overview(StatisticsPeriod.DAYS_7)
        personal = await service.user(listener_id, StatisticsPeriod.DAYS_7)

        assert overview.coverage.partial
        assert overview.coverage.timezone == "Europe/Berlin"
        assert overview.coverage.granularity is ActivityGranularity.DAY
        assert overview.totals.requests.total == 4
        assert overview.totals.requests.manual == 3
        assert overview.totals.requests.radio == 1
        assert overview.totals.playback.overall.started == 2
        assert overview.totals.playback.overall.completed == 1
        assert overview.totals.playback.overall.skipped == 1
        assert overview.totals.playback.manual.skipped == 1
        assert overview.totals.playback.radio.completed == 1
        assert overview.totals.playback_seconds == 180.0
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
        assert overview.top_listeners[0].confirmed_manual_requests == 1
        assert overview.top_listeners[0].unique_tracks == 2
        assert overview.top_listeners[0].discovery_ratio == 1.0
        assert overview.top_listeners[0].repeat_ratio == 0.0
        assert overview.top_listeners[0].radio_share == 0.5
        assert overview.top_listeners[0].presence_seconds == 5400.0
        assert overview.top_listeners[0].achievement_facts.distinct_artists == 1
        assert overview.top_listeners[0].achievement_facts.influenced_tracks == 1
        assert overview.top_listeners[0].achievement_facts.active_listening_days == 1
        assert overview.top_listeners[0].achievement_facts.longest_listening_streak == 1
        owner_ranking = next(
            item for item in overview.top_listeners if item.user_id == seeded.owner_id
        )
        assert owner_ranking.achievement_facts.radio_converted_tracks == 1
        assert overview.active_listeners == 2
        assert {track.track_id for track in overview.requested_tracks} == {
            seeded.first_track_id,
            seeded.second_track_id,
        }
        assert {track.requests for track in overview.requested_tracks} == {2}
        assert overview.requested_artists[0].name == "Shared artist"
        assert overview.requested_artists[0].requests == 4
        assert overview.highlights.most_shared_track is not None
        assert overview.highlights.most_shared_track.track_id in {
            seeded.first_track_id,
            seeded.second_track_id,
        }
        assert overview.highlights.most_shared_track.distinct_listeners == 2
        assert overview.highlights.listener_pair is not None
        assert {
            overview.highlights.listener_pair.first.user_id,
            overview.highlights.listener_pair.second.user_id,
        } == {seeded.owner_id, seeded.listener_id}
        assert overview.highlights.listener_pair.shared_playbacks == 2
        assert overview.highlights.radio_conversion is not None
        assert overview.highlights.radio_conversion.track_id == seeded.first_track_id
        assert overview.highlights.radio_conversion.later_manual_requests == 1
        assert overview.highlights.radio_conversion.distinct_requesters == 1
        assert overview.highlights.contagious_track is not None
        assert overview.highlights.contagious_track.track_id == seeded.second_track_id
        assert (
            overview.highlights.contagious_track.original_requester.user_id
            == seeded.listener_id
        )
        assert overview.highlights.contagious_track.later_manual_requests == 1
        assert overview.highlights.contagious_track.distinct_later_requesters == 1
        assert overview.highlights.busiest_weekday is not None
        assert overview.highlights.busiest_hour is not None
        assert overview.highlights.active_day_streaks.current == 1
        assert overview.highlights.active_day_streaks.longest == 1
        assert overview.highlights.average_listeners == pytest.approx(170 / 180)
        assert overview.library.likes == 1
        assert overview.library.dislikes == 1
        assert overview.library.reactions == 2
        assert overview.library.like_share == 0.5
        assert overview.library.public_playlists == 2
        assert overview.library.shared_playlists == 0
        assert [track.track_id for track in overview.library.top_liked_tracks] == [
            seeded.first_track_id
        ]
        assert [track.track_id for track in overview.library.top_disliked_tracks] == [
            seeded.second_track_id
        ]
        assert [track.track_id for track in overview.library.most_saved_tracks] == [
            seeded.first_track_id
        ]
        assert len(overview.activity) == 7
        assert sum(day.playback_seconds for day in overview.activity) == 180.0
        assert sum(day.listening_seconds for day in overview.activity) == 170.0
        assert sum(day.presence_seconds for day in overview.activity) == 6600.0

        assert personal.user_id == listener_id
        assert personal.totals.requests.total == 2
        assert personal.totals.requests.manual == 1
        assert personal.totals.requests.radio == 1
        assert personal.totals.playback == overview.totals.playback
        assert personal.totals.playback_seconds == 180.0
        assert personal.totals.listening_seconds == 140.0
        assert personal.totals.presence_seconds == 5400.0
        assert sum(day.playback_seconds for day in personal.activity) == 180.0
        assert sum(day.listening_seconds for day in personal.activity) == 140.0
        assert [track.title for track in personal.top_tracks_by_listening] == [
            "First track",
            "Second track",
        ]
        assert personal.top_artists_by_listening[0].name == "Shared artist"
        assert personal.highlights.group_listening_share == pytest.approx(140 / 170)
        assert (
            personal.highlights.listening_pattern.weekdays[4].listening_seconds == 140.0
        )
        assert (
            personal.highlights.listening_pattern.hours[12].listening_seconds == 100.0
        )
        assert personal.highlights.listening_pattern.hours[13].listening_seconds == 40.0
        assert personal.highlights.request_outcomes.manual_requests == 1
        assert personal.highlights.request_outcomes.played_requests == 1
        assert personal.highlights.request_outcomes.completed_requests == 0
        assert personal.highlights.request_outcomes.play_rate == 1.0
        assert personal.highlights.request_outcomes.completion_rate == 0.0
        assert [track.track_id for track in personal.highlights.radio_discoveries] == [
            seeded.first_track_id
        ]
        assert [track.track_id for track in personal.highlights.influenced_tracks] == [
            seeded.second_track_id
        ]
        assert personal.highlights.influenced_tracks[0].later_requests == 1
        assert personal.highlights.influenced_tracks[0].distinct_listeners == 1
        assert {badge.kind for badge in personal.highlights.badges} == {
            ListenerBadgeKind.RESIDENT_DJ
        }

        yearly = await service.overview(StatisticsPeriod.YEAR)
        assert yearly.coverage.granularity is ActivityGranularity.MONTH
        assert len(yearly.activity) == 9
        assert yearly.activity[0].started_on.isoformat() == "2026-01-01"
        assert sum(bucket.playback_seconds for bucket in yearly.activity) == 180.0

        monthly = await service.overview(StatisticsPeriod.DAYS_30)
        assert monthly.coverage.granularity is ActivityGranularity.DAY
        assert len(monthly.activity) == 30
        assert sum(bucket.playback_seconds for bucket in monthly.activity) == 180.0

        all_time = await service.overview(StatisticsPeriod.ALL)
        assert all_time.coverage.granularity is ActivityGranularity.MONTH
        assert not all_time.coverage.partial
        assert all_time.coverage.started_at == NOW - timedelta(hours=2)
        assert sum(bucket.playback_seconds for bucket in all_time.activity) == 180.0

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
        assert profile.library.likes_count == 1
        assert profile.library.dislikes_count == 1
        assert profile.library.public_playlist_count == 2
        assert [track.title for track in profile.library.liked_tracks] == [
            "First track"
        ]
        assert [track.title for track in profile.library.disliked_tracks] == [
            "Second track"
        ]
        assert [playlist.name for playlist in profile.library.public_playlists] == [
            "Collaborative picks",
            "Public favourites",
        ]
        assert profile.library.public_playlists[0].entry_count == 0
        assert profile.library.public_playlists[1].entry_count == 1

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


def test_listener_badges_enforce_samples_and_stable_ties() -> None:
    explorer = _ranked_listener(
        1,
        plays=10,
        unique_tracks=10,
        radio_plays=5,
        confirmed_manual_requests=1,
        listening_seconds=30 * 60,
        night_listening_seconds=0,
    )
    repeater = _ranked_listener(
        2,
        plays=10,
        unique_tracks=5,
        radio_plays=9,
        confirmed_manual_requests=3,
        listening_seconds=30 * 60,
        night_listening_seconds=0,
    )
    same_radio_share = _ranked_listener(
        3,
        plays=10,
        unique_tracks=6,
        radio_plays=9,
        confirmed_manual_requests=2,
        listening_seconds=30 * 60,
        night_listening_seconds=0,
    )
    tiny_sample = _ranked_listener(
        4,
        plays=1,
        unique_tracks=1,
        radio_plays=1,
        confirmed_manual_requests=0,
        listening_seconds=60,
        night_listening_seconds=60,
    )
    regular = _ranked_listener(
        5,
        plays=2,
        unique_tracks=2,
        radio_plays=0,
        confirmed_manual_requests=0,
        presence_seconds=2 * 60 * 60,
        listening_seconds=100,
        night_listening_seconds=0,
    )
    collector = _ranked_listener(
        6,
        plays=60,
        unique_tracks=40,
        radio_plays=20,
        confirmed_manual_requests=25,
        presence_seconds=7 * 60 * 60,
        listening_seconds=6.5 * 60 * 60,
        night_listening_seconds=0,
    )
    night_winner = _ranked_listener(
        7,
        plays=10,
        unique_tracks=10,
        radio_plays=0,
        confirmed_manual_requests=0,
        listening_seconds=30 * 60,
        night_listening_seconds=30 * 60,
    )

    ranked = assign_listener_badges(
        (
            explorer,
            repeater,
            same_radio_share,
            tiny_sample,
            regular,
            collector,
            night_winner,
        )
    )
    badges = {item.user_id: {badge.kind for badge in item.badges} for item in ranked}

    assert badges[explorer.user_id] == {ListenerBadgeKind.EXPLORER}
    assert badges[repeater.user_id] == {
        ListenerBadgeKind.RADIO_REGULAR,
        ListenerBadgeKind.REPEAT_OFFENDER,
    }
    assert badges[same_radio_share.user_id] == set()
    assert badges[tiny_sample.user_id] == set()
    assert badges[regular.user_id] == {ListenerBadgeKind.ALWAYS_AROUND}
    assert badges[night_winner.user_id] == {ListenerBadgeKind.NIGHT_OWL}
    assert badges[collector.user_id] == {
        ListenerBadgeKind.ALWAYS_AROUND,
        ListenerBadgeKind.ALL_EARS,
        ListenerBadgeKind.RESIDENT_DJ,
        ListenerBadgeKind.QUEUE_CURATOR,
        ListenerBadgeKind.RADIO_RIDER,
        ListenerBadgeKind.WIDE_ROTATION,
        ListenerBadgeKind.LONG_HAUL,
        ListenerBadgeKind.QUEUE_ARCHITECT,
        ListenerBadgeKind.LOCKED_IN,
    }


def test_listener_badges_add_period_bound_earned_achievements() -> None:
    listener = _ranked_listener(
        1,
        plays=8,
        unique_tracks=8,
        radio_plays=8,
        confirmed_manual_requests=3,
        presence_seconds=0,
        listening_seconds=4 * 60 * 60,
        night_listening_seconds=0,
        achievement_facts=ListenerAchievementFacts(
            dawn_listening_seconds=60 * 60,
            weekend_listening_seconds=2 * 60 * 60,
            distinct_artists=20,
            influenced_tracks=3,
            radio_converted_tracks=3,
            active_listening_days=4,
            longest_listening_streak=3,
        ),
    )

    [ranked] = assign_listener_badges((listener,))
    badges = {badge.kind: badge for badge in ranked.badges}

    earned_kinds = tuple(
        kind
        for kind in badges
        if kind
        in {
            ListenerBadgeKind.TASTE_MAKER,
            ListenerBadgeKind.RADIO_CONVERT,
            ListenerBadgeKind.DAWN_PATROL,
            ListenerBadgeKind.WEEKEND_REGULAR,
            ListenerBadgeKind.ARTIST_EXPLORER,
            ListenerBadgeKind.LISTENING_STREAK,
        }
    )
    assert earned_kinds == (
        ListenerBadgeKind.TASTE_MAKER,
        ListenerBadgeKind.RADIO_CONVERT,
        ListenerBadgeKind.DAWN_PATROL,
        ListenerBadgeKind.WEEKEND_REGULAR,
        ListenerBadgeKind.ARTIST_EXPLORER,
        ListenerBadgeKind.LISTENING_STREAK,
    )
    assert badges[ListenerBadgeKind.DAWN_PATROL].value == 60 * 60
    assert badges[ListenerBadgeKind.DAWN_PATROL].sample_size == 4 * 60 * 60
    assert badges[ListenerBadgeKind.TASTE_MAKER].value == 3
    assert badges[ListenerBadgeKind.TASTE_MAKER].sample_size == 3
    assert badges[ListenerBadgeKind.LISTENING_STREAK].value == 3
    assert badges[ListenerBadgeKind.LISTENING_STREAK].sample_size == 4


def test_active_day_streaks_end_on_the_current_local_day() -> None:
    streaks = calculate_active_day_streaks(
        (
            date(2026, 9, 20),
            date(2026, 9, 21),
            date(2026, 9, 23),
            date(2026, 9, 24),
            date(2026, 9, 25),
        ),
        date(2026, 9, 25),
    )
    assert streaks.current == 3
    assert streaks.longest == 3

    inactive_today = calculate_active_day_streaks(
        (date(2026, 9, 23), date(2026, 9, 24)),
        date(2026, 9, 25),
    )
    assert inactive_today.current == 0
    assert inactive_today.longest == 2


def _ranked_listener(
    identity: int,
    *,
    plays: int,
    unique_tracks: int,
    radio_plays: int,
    confirmed_manual_requests: int,
    presence_seconds: float | None = None,
    listening_seconds: float,
    night_listening_seconds: float,
    achievement_facts: ListenerAchievementFacts | None = None,
) -> RankedListener:
    return RankedListener(
        user_id=UserId(UUID(int=identity)),
        discord_id=str(identity),
        display_name=f"Listener {identity}",
        discord_username=f"listener-{identity}",
        discord_avatar_hash=None,
        manual_requests=confirmed_manual_requests,
        confirmed_manual_requests=confirmed_manual_requests,
        plays=plays,
        unique_tracks=unique_tracks,
        radio_plays=radio_plays,
        presence_seconds=(
            listening_seconds if presence_seconds is None else presence_seconds
        ),
        listening_seconds=listening_seconds,
        night_listening_seconds=night_listening_seconds,
        achievement_facts=achievement_facts or ListenerAchievementFacts(),
    )

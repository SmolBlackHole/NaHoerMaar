# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import insert

from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.listening.domain import PlaybackEndReason
from nahoermaar.users.domain import (
    AccessRole,
    DiscordIdentity,
    User,
    UserId,
    UserProfile,
)
from nahoermaar.users.repository import UserRepository
from nahoermaar.views.history import PlaybackHistoryView

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_history_keeps_repeated_starts_searchable_and_snapshot_stable() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    view = PlaybackHistoryView(units)
    user_id = UserId(uuid4())
    radio_user_id = UserId(uuid4())
    session_id = uuid4()
    artist_id = uuid4()
    other_artist_id = uuid4()
    still_alive_id = uuid4()
    other_track_id = uuid4()
    radio_source_id = uuid4()
    radio_run_id = uuid4()
    still_alive_request_id = uuid4()
    other_request_id = uuid4()
    playback_ids = tuple(uuid4() for _ in range(105))

    async def scenario() -> None:
        async with units() as work:
            users = UserRepository(work.session)
            users.add(
                User(
                    user_id,
                    DiscordIdentity("9", "andrey", None, NOW),
                    NOW,
                    NOW,
                    profile=UserProfile("Andrey"),
                    role=AccessRole.OWNER,
                )
            )
            users.add(
                User(
                    radio_user_id,
                    DiscordIdentity("10", "radio-listener", None, NOW),
                    NOW,
                    NOW,
                    profile=UserProfile("Radio Listener"),
                    role=AccessRole.USER,
                    access_granted_by=user_id,
                    access_granted_at=NOW,
                )
            )
            await work.session.flush()
            await work.session.execute(
                insert(Base.metadata.tables["artists"]),
                (
                    {
                        "id": artist_id,
                        "name": "Mt. Eden",
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                    {
                        "id": other_artist_id,
                        "name": "Someone Else",
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                ),
            )
            await work.session.execute(
                insert(Base.metadata.tables["tracks"]),
                (
                    {
                        "id": still_alive_id,
                        "title": "Still Alive",
                        "duration_seconds": 248.0,
                        "artwork_url": "https://example.test/still-alive.jpg",
                        "album_title": None,
                        "release_date": None,
                        "isrc": None,
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                    {
                        "id": other_track_id,
                        "title": "Other Song",
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
                    {
                        "track_id": still_alive_id,
                        "position": 0,
                        "artist_id": artist_id,
                    },
                    {
                        "track_id": other_track_id,
                        "position": 0,
                        "artist_id": other_artist_id,
                    },
                ),
            )
            await work.session.execute(
                insert(Base.metadata.tables["track_sources"]).values(
                    id=radio_source_id,
                    track_id=other_track_id,
                    provider="youtube",
                    external_id="radio-track",
                    source_url="https://example.test/radio-track",
                    observed_title="Other Song",
                    observed_artist="Someone Else",
                    observed_duration_seconds=180.0,
                    observed_artwork_url=None,
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
                    seed_track_source_id=radio_source_id,
                    seed_discovery_snapshot_id=None,
                    initiated_by=radio_user_id,
                    started_at=NOW - timedelta(days=1),
                    ended_at=NOW - timedelta(hours=23),
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
                        "id": still_alive_request_id,
                        "session_id": session_id,
                        "track_id": still_alive_id,
                        "source_id": None,
                        "requested_at": NOW - timedelta(days=1),
                        "origin": "manual",
                        "requested_by": user_id,
                        "radio_run_id": None,
                    },
                    {
                        "id": other_request_id,
                        "session_id": session_id,
                        "track_id": other_track_id,
                        "source_id": radio_source_id,
                        "requested_at": NOW - timedelta(days=1),
                        "origin": "radio",
                        "requested_by": radio_user_id,
                        "radio_run_id": radio_run_id,
                    },
                ),
            )
            await work.session.execute(
                insert(Base.metadata.tables["playback_records"]),
                (
                    *(
                        {
                            "id": playback_id,
                            "session_id": session_id,
                            "request_id": still_alive_request_id,
                            "started_at": NOW - timedelta(minutes=index),
                            "audio_seconds": 120.0,
                            "group_audio_seconds": 100.0,
                            "ended_at": NOW
                            - timedelta(minutes=index)
                            + timedelta(seconds=120),
                            "end_reason": "skipped" if index == 0 else "completed",
                        }
                        for index, playback_id in enumerate(playback_ids)
                    ),
                    {
                        "id": uuid4(),
                        "session_id": session_id,
                        "request_id": other_request_id,
                        "started_at": NOW - timedelta(days=2),
                        "audio_seconds": 180.0,
                        "group_audio_seconds": 160.0,
                        "ended_at": NOW - timedelta(days=2) + timedelta(seconds=180),
                        "end_reason": "completed",
                    },
                ),
            )
            await work.commit()

        first = await view.get(page=1, page_size=10)
        assert first.total == 106
        assert first.page_count == 11
        assert len(first.entries) == 10
        assert len({entry.playback_id for entry in first.entries}) == 10
        assert first.snapshot is not None
        assert {contributor.user_id for contributor in first.contributors} == {
            user_id,
            radio_user_id,
        }

        new_playback_id = uuid4()
        async with units() as work:
            await work.session.execute(
                insert(Base.metadata.tables["playback_records"]).values(
                    id=new_playback_id,
                    session_id=session_id,
                    request_id=still_alive_request_id,
                    started_at=NOW + timedelta(minutes=1),
                    audio_seconds=0.0,
                    group_audio_seconds=0.0,
                    ended_at=None,
                    end_reason=None,
                )
            )
            await work.commit()

        second = await view.get(
            page=2,
            page_size=10,
            snapshot=first.snapshot,
        )
        assert second.total == 106
        assert new_playback_id not in {entry.playback_id for entry in second.entries}
        assert not {entry.playback_id for entry in first.entries} & {
            entry.playback_id for entry in second.entries
        }

        title_search = await view.get(page=11, page_size=10, query="still ALIVE")
        assert title_search.total == 106
        assert len(title_search.entries) == 6
        assert all(entry.title == "Still Alive" for entry in title_search.entries)

        artist_search = await view.get(page=11, page_size=10, query="mt. EDEN")
        assert artist_search.total == 106
        assert len(artist_search.entries) == 6
        assert all(
            entry.artist_names == ("Mt. Eden",) for entry in artist_search.entries
        )

        time_window = await view.get(
            page=1,
            page_size=10,
            started_from=NOW - timedelta(minutes=4),
            started_to=NOW - timedelta(minutes=2),
        )
        assert time_window.total == 3
        assert all(
            NOW - timedelta(minutes=4) <= entry.started_at <= NOW - timedelta(minutes=2)
            for entry in time_window.entries
        )

        skipped = await view.get(
            page=1,
            page_size=10,
            end_reason=PlaybackEndReason.SKIPPED,
        )
        assert skipped.total == 1
        assert skipped.entries[0].end_reason is PlaybackEndReason.SKIPPED

        with pytest.raises(ValueError, match="start cannot follow"):
            await view.get(
                started_from=NOW,
                started_to=NOW - timedelta(seconds=1),
            )

        radio_only = await view.get(page=1, page_size=10, radio=True)
        assert radio_only.total == 1
        assert radio_only.entries[0].origin.value == "radio"

        without_radio = await view.get(page=1, page_size=10, radio=False)
        assert without_radio.total == 106
        assert all(entry.origin.value == "manual" for entry in without_radio.entries)

        radio_listener = await view.get(
            page=1,
            page_size=10,
            requested_by=radio_user_id,
        )
        assert radio_listener.total == 1
        assert radio_listener.entries[0].requested_by == radio_user_id

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

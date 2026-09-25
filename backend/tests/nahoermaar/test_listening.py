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
from sqlalchemy import func, insert, select

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.listening.domain import (
    ListeningError,
    ListeningErrorCode,
    PlaybackEndReason,
    PlaybackProgress,
    PlaybackRecordId,
)
from nahoermaar.listening.repository import ListeningRepository
from nahoermaar.listening.service import (
    AdvancePlayback,
    AudienceChanged,
    AudienceUnavailable,
    BeginPlayback,
    DisconnectAudience,
    FinishPlayback,
    ListeningService,
    ObserveAudience,
    PlaybackAdvanced,
    PlaybackEnded,
    PlaybackStarted,
    VoiceMemberState,
)
from nahoermaar.messaging import Event, MessageBus, MessageContext
from nahoermaar.player.domain import (
    ListeningSessionId,
    RequestOrigin,
    TrackRequest,
    TrackRequestId,
)
from nahoermaar.users.domain import UserId

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
LISTENER_DISCORD_ID = "100"
BLOCKED_DISCORD_ID = "200"
BOT_DISCORD_ID = "300"


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


async def _seed(
    database: Database,
    *,
    request_count: int = 2,
) -> tuple[ListeningSessionId, UserId, tuple[TrackRequest, ...]]:
    owner_id = UserId(uuid4())
    listener_id = UserId(uuid4())
    blocked_id = UserId(uuid4())
    session_id = ListeningSessionId(uuid4())
    tracks = tuple(TrackId(uuid4()) for _ in range(request_count))
    requests = tuple(
        TrackRequest(
            TrackRequestId(uuid4()),
            session_id,
            track_id,
            None,
            NOW,
            RequestOrigin.MANUAL,
            listener_id,
            None,
        )
        for track_id in tracks
    )

    async with UnitOfWork(database.sessions) as work:
        await work.session.execute(
            insert(Base.metadata.tables["users"]),
            (
                {
                    "id": owner_id,
                    "role": "owner",
                    "access_granted_by": None,
                    "access_granted_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                    "last_login_at": None,
                },
                {
                    "id": listener_id,
                    "role": "user",
                    "access_granted_by": owner_id,
                    "access_granted_at": NOW,
                    "created_at": NOW,
                    "updated_at": NOW,
                    "last_login_at": None,
                },
                {
                    "id": blocked_id,
                    "role": None,
                    "access_granted_by": None,
                    "access_granted_at": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                    "last_login_at": None,
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["discord_identities"]),
            (
                {
                    "user_id": listener_id,
                    "discord_id": LISTENER_DISCORD_ID,
                    "username": None,
                    "avatar_hash": None,
                    "synced_at": None,
                },
                {
                    "user_id": blocked_id,
                    "discord_id": BLOCKED_DISCORD_ID,
                    "username": None,
                    "avatar_hash": None,
                    "synced_at": None,
                },
            ),
        )
        await work.session.execute(
            insert(Base.metadata.tables["tracks"]),
            tuple(
                {
                    "id": track_id,
                    "title": f"Track {index}",
                    "duration_seconds": 180.0,
                    "artwork_url": None,
                    "album_title": None,
                    "release_date": None,
                    "isrc": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for index, track_id in enumerate(tracks)
            ),
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
        await ListeningRepository(work.session).add_requests(requests)
        await work.commit()
    return session_id, listener_id, requests


def _service(
    database: Database,
) -> tuple[MessageBus, ListeningService, list[Event]]:
    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    bus = MessageBus()
    service = ListeningService(units, bus)
    events: list[Event] = []

    async def capture(event: Event, _context: MessageContext) -> None:
        events.append(event)

    bus.register_command(BeginPlayback, service.begin)
    bus.register_command(AdvancePlayback, service.advance)
    bus.register_command(FinishPlayback, service.finish)
    bus.register_command(ObserveAudience, service.observe)
    bus.register_command(DisconnectAudience, service.disconnect)
    bus.subscribe(PlaybackStarted, capture)
    bus.subscribe(PlaybackAdvanced, capture)
    bus.subscribe(PlaybackEnded, capture)
    bus.subscribe(AudienceChanged, capture)
    bus.subscribe(AudienceUnavailable, capture)
    return bus, service, events


def _voice(
    *, listener: bool = True, blocked: bool = False, deafened: bool = False
) -> tuple[VoiceMemberState, ...]:
    members: list[VoiceMemberState] = []
    if listener:
        members.append(VoiceMemberState(LISTENER_DISCORD_ID, False, deafened))
    if blocked:
        members.append(VoiceMemberState(BLOCKED_DISCORD_ID, False, False))
    members.append(VoiceMemberState(BOT_DISCORD_ID, True, False))
    return tuple(members)


def test_requests_become_plays_only_after_audio_and_progress_is_idempotent() -> None:
    database = _database()

    async def scenario() -> None:
        session_id, listener_id, requests = await _seed(database)
        bus, service, events = _service(database)
        await service.start(session_id)

        async with UnitOfWork(database.sessions) as work:
            count = await work.session.scalar(
                select(func.count()).select_from(
                    Base.metadata.tables["playback_records"]
                )
            )
            assert count == 0

        audience = await bus.execute(
            ObserveAudience(session_id, _voice(blocked=True), NOW)
        )
        assert audience.human_count == 2
        assert audience.audible_human_count == 2
        assert audience.user_ids == frozenset({listener_id})

        playback_id = PlaybackRecordId(uuid4())
        begin = BeginPlayback(playback_id, session_id, requests[0].id, NOW)
        assert await bus.execute(begin) == await bus.execute(begin)
        assert sum(isinstance(event, PlaybackStarted) for event in events) == 1

        unchanged = AdvancePlayback(
            session_id,
            (PlaybackProgress(playback_id, 0.0),),
            playback_id,
            NOW + timedelta(seconds=3),
        )
        await bus.execute(unchanged)
        assert not any(isinstance(event, PlaybackAdvanced) for event in events)

        progressed = AdvancePlayback(
            session_id,
            (PlaybackProgress(playback_id, 5.0),),
            playback_id,
            NOW + timedelta(seconds=5),
        )
        await bus.execute(progressed)
        await bus.execute(progressed)
        assert sum(isinstance(event, PlaybackAdvanced) for event in events) == 1

        await bus.execute(
            ObserveAudience(
                session_id,
                _voice(blocked=True, deafened=True),
                NOW + timedelta(seconds=6),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 8.0),),
                playback_id,
                NOW + timedelta(seconds=9),
            )
        )
        ended = FinishPlayback(
            playback_id,
            NOW + timedelta(seconds=10),
            PlaybackEndReason.FAILED,
        )
        await bus.execute(ended)
        await bus.execute(ended)

        retry_id = PlaybackRecordId(uuid4())
        await bus.execute(
            BeginPlayback(
                retry_id,
                session_id,
                requests[0].id,
                NOW + timedelta(seconds=11),
            )
        )

        async with UnitOfWork(database.sessions) as work:
            repository = ListeningRepository(work.session)
            first = await repository.playback(playback_id)
            assert first is not None
            assert first.audio_seconds == 8.0
            assert first.group_audio_seconds == 8.0
            assert first.end_reason is PlaybackEndReason.FAILED
            assert await repository.playback(retry_id) is not None
            listeners = await repository.playback_listeners(playback_id)
            assert [(item.user_id, item.audio_seconds) for item in listeners] == [
                (listener_id, 5.0)
            ]
            presence = await repository.presence_history(session_id)
            assert {item.user_id for item in presence} == {listener_id}
            request_two_plays = await work.session.scalar(
                select(func.count())
                .select_from(Base.metadata.tables["playback_records"])
                .where(
                    Base.metadata.tables["playback_records"].c.request_id
                    == requests[0].id
                )
            )
            request_without_audio = await work.session.scalar(
                select(func.count())
                .select_from(Base.metadata.tables["playback_records"])
                .where(
                    Base.metadata.tables["playback_records"].c.request_id
                    == requests[1].id
                )
            )
            assert request_two_plays == 2
            assert request_without_audio == 0

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_join_leave_pause_seek_and_stall_do_not_inflate_listening_time() -> None:
    database = _database()

    async def scenario() -> None:
        session_id, listener_id, requests = await _seed(database, request_count=1)
        bus, service, _events = _service(database)
        await service.start(session_id)
        playback_id = PlaybackRecordId(uuid4())
        await bus.execute(BeginPlayback(playback_id, session_id, requests[0].id, NOW))
        await bus.execute(ObserveAudience(session_id, _voice(listener=False), NOW))
        await bus.execute(
            ObserveAudience(
                session_id,
                _voice(),
                NOW + timedelta(seconds=2),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 5.0),),
                playback_id,
                NOW + timedelta(seconds=5),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 7.0),),
                playback_id,
                NOW + timedelta(seconds=7),
            )
        )
        await bus.execute(
            ObserveAudience(
                session_id,
                _voice(listener=False),
                NOW + timedelta(seconds=7),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 7.0),),
                playback_id,
                NOW + timedelta(seconds=30),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 10.0),),
                playback_id,
                NOW + timedelta(seconds=33),
            )
        )

        async with UnitOfWork(database.sessions) as work:
            listeners = await ListeningRepository(work.session).playback_listeners(
                playback_id
            )
            assert [(item.user_id, item.audio_seconds) for item in listeners] == [
                (listener_id, 5.0)
            ]

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_disconnect_and_restart_require_fresh_audience_before_crediting() -> None:
    database = _database()

    async def scenario() -> None:
        session_id, listener_id, requests = await _seed(database, request_count=1)
        bus, service, events = _service(database)
        await service.start(session_id)
        playback_id = PlaybackRecordId(uuid4())
        await bus.execute(BeginPlayback(playback_id, session_id, requests[0].id, NOW))
        await bus.execute(ObserveAudience(session_id, _voice(), NOW))
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 4.0),),
                playback_id,
                NOW + timedelta(seconds=4),
            )
        )
        unavailable = await bus.execute(
            DisconnectAudience(session_id, NOW + timedelta(seconds=5))
        )
        assert unavailable.empty is None
        assert isinstance(events[-1], AudienceUnavailable)
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 6.0),),
                playback_id,
                NOW + timedelta(seconds=7),
            )
        )
        await bus.execute(
            ObserveAudience(session_id, _voice(), NOW + timedelta(seconds=8))
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 8.0),),
                playback_id,
                NOW + timedelta(seconds=10),
            )
        )

        restarted_bus, restarted, _restarted_events = _service(database)
        await restarted.start(session_id)
        assert restarted.audience.empty is None
        await restarted_bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 9.0),),
                playback_id,
                NOW + timedelta(seconds=11),
            )
        )
        await restarted_bus.execute(
            ObserveAudience(session_id, _voice(), NOW + timedelta(seconds=12))
        )
        await restarted_bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 10.0),),
                playback_id,
                NOW + timedelta(seconds=13),
            )
        )

        async with UnitOfWork(database.sessions) as work:
            repository = ListeningRepository(work.session)
            listeners = await repository.playback_listeners(playback_id)
            assert [(item.user_id, item.audio_seconds) for item in listeners] == [
                (listener_id, 7.0)
            ]
            history = await repository.presence_history(session_id)
            assert len(history) == 3
            assert history[0].left_at == NOW + timedelta(seconds=5)
            assert history[1].left_at == NOW + timedelta(seconds=10)
            assert history[2].active

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_crossfade_advances_both_plays_but_credits_real_time_once() -> None:
    database = _database()

    async def scenario() -> None:
        session_id, listener_id, requests = await _seed(database)
        bus, service, _events = _service(database)
        await service.start(session_id)
        outgoing_id = PlaybackRecordId(uuid4())
        incoming_id = PlaybackRecordId(uuid4())
        await bus.execute(BeginPlayback(outgoing_id, session_id, requests[0].id, NOW))
        await bus.execute(ObserveAudience(session_id, _voice(), NOW))
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(outgoing_id, 5.0),),
                outgoing_id,
                NOW + timedelta(seconds=5),
            )
        )
        await bus.execute(
            BeginPlayback(
                incoming_id,
                session_id,
                requests[1].id,
                NOW + timedelta(seconds=5),
            )
        )
        await bus.execute(
            AdvancePlayback(
                session_id,
                (
                    PlaybackProgress(outgoing_id, 7.0),
                    PlaybackProgress(incoming_id, 2.0),
                ),
                incoming_id,
                NOW + timedelta(seconds=7),
            )
        )

        async with UnitOfWork(database.sessions) as work:
            repository = ListeningRepository(work.session)
            outgoing = await repository.playback(outgoing_id)
            incoming = await repository.playback(incoming_id)
            assert outgoing is not None and outgoing.audio_seconds == 7.0
            assert incoming is not None and incoming.audio_seconds == 2.0
            assert outgoing.group_audio_seconds == 5.0
            assert incoming.group_audio_seconds == 2.0
            assert outgoing.group_audio_seconds + incoming.group_audio_seconds == 7.0
            outgoing_listeners = await repository.playback_listeners(outgoing_id)
            incoming_listeners = await repository.playback_listeners(incoming_id)
            assert [
                (item.user_id, item.audio_seconds) for item in outgoing_listeners
            ] == [(listener_id, 5.0)]
            assert [
                (item.user_id, item.audio_seconds) for item in incoming_listeners
            ] == [(listener_id, 2.0)]
            assert (
                sum(
                    item.audio_seconds
                    for item in (*outgoing_listeners, *incoming_listeners)
                )
                == 7.0
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())


def test_progress_rejects_regression() -> None:
    database = _database()

    async def scenario() -> None:
        session_id, _listener_id, requests = await _seed(database, request_count=1)
        bus, service, _events = _service(database)
        await service.start(session_id)
        playback_id = PlaybackRecordId(uuid4())
        await bus.execute(BeginPlayback(playback_id, session_id, requests[0].id, NOW))
        await bus.execute(ObserveAudience(session_id, _voice(), NOW))
        await bus.execute(
            AdvancePlayback(
                session_id,
                (PlaybackProgress(playback_id, 3.0),),
                playback_id,
                NOW + timedelta(seconds=3),
            )
        )
        with pytest.raises(ListeningError) as caught:
            await bus.execute(
                AdvancePlayback(
                    session_id,
                    (PlaybackProgress(playback_id, 2.0),),
                    playback_id,
                    NOW + timedelta(seconds=4),
                )
            )
        assert caught.value.code is ListeningErrorCode.PROGRESS_REGRESSION

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())

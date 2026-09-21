# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from nahormaar_backend.api.schemas import State
from nahormaar_backend.application.audio import ResolvedTrack, TrackError
from nahormaar_backend.application.player import Player
from nahormaar_backend.application.session import Session
from nahormaar_backend.domain.fsm import (
    LifecycleEvent,
    PlaybackContext,
    PlaybackEffect,
    decide_playback,
)
from nahormaar_backend.domain.models import (
    HISTORY_LIMIT,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import (
    ControlledResolver,
    FakeVoice,
    _wait_until,  # pyright: ignore[reportPrivateUsage]
)


def test_history_records_starts_and_survives_recovery(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    with SQLiteStore(path) as store:
        player = Player(store)
        removed = QueueEntry("https://youtu.be/removed")
        started = QueueEntry("https://youtu.be/started")
        player.enqueue(removed)
        player.remove((removed,))
        player.commit_lifecycle(PlayerSnapshot(PlaybackState.LOADING, started), None)
        assert player.snapshot.recently_played == ()
        player.enrich(
            started.id,
            TrackMetadata(title="Song", artist="Artist", duration_seconds=123),
        )
        player.commit_lifecycle(
            replace(player.snapshot, state=PlaybackState.PLAYING),
            None,
            record_history=True,
        )
        player.commit_lifecycle(
            replace(player.snapshot, state=PlaybackState.PAUSED), None
        )
        player.commit_lifecycle(
            replace(player.snapshot, state=PlaybackState.PLAYING), None
        )
        history = player.snapshot.recently_played
        assert len(history) == 1
        assert history[0].entry.title == "Song"
        assert history[0].entry.artist == "Artist"
        assert history[0].entry.duration_seconds == 123
        assert history[0].played_at.utcoffset() is not None
    with SQLiteStore(path) as store:
        player = Player(store)
        assert player.snapshot.recently_played == history
        assert player.snapshot.upcoming == (history[0].entry,)
        player.commit_lifecycle(
            replace(
                player.snapshot,
                state=PlaybackState.PLAYING,
                current=player.snapshot.upcoming[0],
                upcoming=(),
            ),
            None,
            record_history=True,
        )
        assert len(player.snapshot.recently_played) == 2
        assert player.snapshot.recently_played[0].id != history[0].id
        player.commit_lifecycle(
            replace(player.snapshot, state=PlaybackState.IDLE, current=None), None
        )
        assert len(player.snapshot.recently_played) == 2


def test_failed_and_removed_tracks_never_enter_history(player: Player) -> None:
    entry = QueueEntry("https://youtu.be/missing")
    attempt = uuid4()
    player.commit_lifecycle(PlayerSnapshot(PlaybackState.LOADING, entry), None)
    failed = decide_playback(
        player.snapshot,
        PlaybackContext(entry_id=entry.id, attempt_id=attempt),
        LifecycleEvent.FAILED,
        attempt_id=attempt,
    )
    assert PlaybackEffect.RECORD_HISTORY not in failed.effects
    player.commit_lifecycle(failed.snapshot, None)
    player.enrich(entry.id, TrackMetadata(title="Late metadata"))
    assert player.snapshot.recently_played == ()
    assert player.snapshot.current is None
    assert player.snapshot.upcoming == ()


def test_history_is_bounded_and_newest_first(
    player: Player, store: SQLiteStore
) -> None:
    for index in range(HISTORY_LIMIT + 2):
        player.commit_lifecycle(
            replace(
                player.snapshot,
                state=PlaybackState.PLAYING,
                current=QueueEntry(f"https://youtu.be/{index}"),
            ),
            None,
            record_history=True,
        )
    history = store.load().recently_played
    assert len(history) == HISTORY_LIMIT
    assert history[0].entry.source_url.endswith(str(HISTORY_LIMIT + 1))
    assert history[-1].entry.source_url.endswith("/2")


def test_playback_publishes_metadata_and_retry_does_not_duplicate_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        resolver, voice = ControlledResolver(), FakeVoice()
        controller = await Session.create(
            lambda: SQLiteStore(tmp_path / "player.sqlite3"), resolver, voice
        )
        try:
            await controller.enqueue(QueueEntry("https://youtu.be/Pqp9fDRp1lw"))
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.requests[0].set_result(
                ResolvedTrack(
                    "https://stream.invalid/secret",
                    title="Song",
                    uploader="Channel",
                    duration_seconds=200,
                    artist="Artist",
                    thumbnail_url="https://i.ytimg.com/cover.jpg",
                    uploader_url="https://www.youtube.com/channel/example",
                )
            )
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )
            state = State.from_status(controller.status)
            assert state.current is not None
            assert state.current.title == "Song"
            assert state.current.artist == "Artist"
            assert state.current.duration_seconds == 200
            assert state.recently_played[0].entry == state.current
            assert "secret" not in state.model_dump_json()
            voice.complete(0, TrackError("retry", retryable=True))
            await _wait_until(lambda: len(resolver.requests) == 2)
            resolver.succeed(1)
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )
            assert len(controller.snapshot.recently_played) == 1
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_background_metadata_is_serial_and_does_not_resurrect_removed_entries(
    tmp_path: Path,
) -> None:
    class Metadata:
        def __init__(self) -> None:
            self.requests: list[asyncio.Future[TrackMetadata]] = []

        async def metadata(self, source_url: str) -> TrackMetadata:
            request: asyncio.Future[TrackMetadata] = (
                asyncio.get_running_loop().create_future()
            )
            self.requests.append(request)
            return await request

    async def scenario() -> None:
        metadata = Metadata()
        controller = await Session.create(
            lambda: SQLiteStore(tmp_path / "player.sqlite3"),
            ControlledResolver(),
            FakeVoice(),
            metadata_resolver=metadata,
        )
        try:
            first, second = (
                QueueEntry("https://youtu.be/first"),
                QueueEntry("https://youtu.be/second"),
            )
            await controller.enqueue(first)
            await controller.enqueue(second)
            await _wait_until(lambda: len(metadata.requests) == 1)
            await controller.remove(first.id)
            queue_revision = controller.status.queue_revision
            metadata.requests[0].set_result(
                TrackMetadata(title="Removed", duration_seconds=10)
            )
            await _wait_until(lambda: len(metadata.requests) == 2)
            metadata.requests[1].set_result(
                TrackMetadata(title="Upcoming", duration_seconds=20)
            )
            await _wait_until(
                lambda: controller.snapshot.upcoming[0].title == "Upcoming"
            )
            assert len(controller.snapshot.upcoming) == 1
            assert controller.snapshot.upcoming[0].id == second.id
            assert controller.status.queue_revision == queue_revision
            assert controller.snapshot.recently_played == ()
        finally:
            await controller.close()

    asyncio.run(scenario())

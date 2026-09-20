# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path

from nahormaar_backend.api.schemas import State
from nahormaar_backend.application.audio import ResolvedTrack, TrackError
from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.application.player import Player
from nahormaar_backend.domain.models import (
    HISTORY_LIMIT,
    PlaybackState,
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
        player.remove(removed.id)
        player.enqueue(started)
        player.play()
        assert player.snapshot.recently_played == ()
        player.enrich(
            started.id,
            TrackMetadata(title="Song", artist="Artist", duration_seconds=123),
        )
        player.mark_playing()
        player.pause()
        player.play()
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
        player.play()
        player.mark_playing()
        assert len(player.snapshot.recently_played) == 2
        assert player.snapshot.recently_played[0].id != history[0].id
        player.skip()
        assert len(player.snapshot.recently_played) == 2


def test_failed_and_removed_tracks_never_enter_history(player: Player) -> None:
    entry = QueueEntry("https://youtu.be/missing")
    player.enqueue(entry)
    player.play()
    player.fail()
    player.skip()
    player.enrich(entry.id, TrackMetadata(title="Late metadata"))
    assert player.snapshot.recently_played == ()
    assert player.snapshot.current is None
    assert player.snapshot.upcoming == ()


def test_history_is_bounded_and_newest_first(
    player: Player, store: SQLiteStore
) -> None:
    for index in range(HISTORY_LIMIT + 2):
        player.enqueue(QueueEntry(f"https://youtu.be/{index}"))
        player.play()
        player.mark_playing()
        player.skip()
    history = store.load().recently_played
    assert len(history) == HISTORY_LIMIT
    assert history[0].entry.source_url.endswith(str(HISTORY_LIMIT + 1))
    assert history[-1].entry.source_url.endswith("/2")


def test_playback_publishes_metadata_and_retry_does_not_duplicate_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        resolver, voice = ControlledResolver(), FakeVoice()
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3", resolver, voice
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
        controller = await PlaybackController.create(
            tmp_path / "player.sqlite3",
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

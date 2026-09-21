# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Radio acceptance with an in-memory mutation owner, without audio or storage."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nahormaar_backend.application.catalog import source_key
from nahormaar_backend.application.radio import (
    RadioController,
    RadioLoaded,
    RadioMessage,
    RadioPreview,
    RefillRadio,
)
from nahormaar_backend.domain import queue
from nahormaar_backend.domain.catalog import CatalogTrack
from nahormaar_backend.domain.models import (
    Contributor,
    HistoryEntry,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
)
from nahormaar_backend.domain.radio import RadioSeed, RadioState

ACTOR = Contributor(uuid4(), "Listener", "0001")
SEED = RadioSeed("track", "Pqp9fDRp1lw", "Seed")


def tracks(start: int = 0) -> tuple[CatalogTrack, ...]:
    return tuple(
        CatalogTrack(
            video_id=f"{i:011}",
            source_url=f"https://youtu.be/{i:011}",
            title=f"Track {i}",
        )
        for i in range(start, start + 8)
    )


class Provider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.returned = asyncio.Event()
        self.responses: list[asyncio.Future[tuple[CatalogTrack, ...]]] = []
        self.calls = 0
        self.late = False

    async def recommend(self, seed: RadioSeed, limit: int) -> tuple[CatalogTrack, ...]:
        self.calls += 1
        response: asyncio.Future[tuple[CatalogTrack, ...]] = (
            asyncio.get_running_loop().create_future()
        )
        self.responses.append(response)
        self.started.set()
        try:
            return await asyncio.shield(response)
        except asyncio.CancelledError:
            if self.late:
                return tracks()
            raise
        finally:
            self.returned.set()

    @property
    def response(self) -> asyncio.Future[tuple[CatalogTrack, ...]]:
        return self.responses[-1]


@dataclass(frozen=True)
class Start:
    entries: tuple[CatalogTrack, ...]


@dataclass(frozen=True)
class Edit:
    entries: tuple[QueueEntry, ...] = ()
    removed: tuple[QueueEntry, ...] = ()
    snapshot: PlayerSnapshot | None = None


@dataclass(frozen=True)
class Stop:
    pass


@dataclass(frozen=True)
class Retry:
    pass


type HarnessMessage = RadioMessage | Start | Edit | Stop | Retry


class RadioHarness:
    def __init__(self, snapshot: PlayerSnapshot | None = None) -> None:
        self.snapshot = snapshot if snapshot is not None else PlayerSnapshot()
        self.provider = Provider()
        self.inbox: asyncio.Queue[
            tuple[HarnessMessage, asyncio.Future[None]] | None
        ] = asyncio.Queue()
        self.closed = False
        self.play_calls = 0
        self.connected = True
        self.hold_loaded_ack: asyncio.Event | None = None
        self.handled: list[HarnessMessage] = []
        self.radio = RadioController(
            self.provider,
            snapshot=lambda: self.snapshot,
            source_key=source_key,
            available=lambda: not self.closed,
            send=self.send,
            enqueue=self.enqueue,
            connected=lambda: self.connected,
            play=self.play,
        )
        self.actor = asyncio.create_task(self.run())

    def post(self, message: HarnessMessage) -> asyncio.Future[None]:
        if self.closed:
            raise RuntimeError("Owner closed")
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        done.add_done_callback(
            lambda future: None if future.cancelled() else future.exception()
        )
        self.inbox.put_nowait((message, done))
        return done

    async def send(self, message: RadioMessage) -> None:
        await self.post(message)
        if isinstance(message, RadioLoaded) and self.hold_loaded_ack is not None:
            await self.hold_loaded_ack.wait()

    async def run(self) -> None:
        while item := await self.inbox.get():
            message, done = item
            try:
                if self.closed:
                    raise RuntimeError("Owner closed")
                before = (self.snapshot, self.radio.status)
                match message:
                    case Start(initial_entries):
                        self.radio.start(
                            RadioPreview(uuid4(), SEED, initial_entries), ACTOR
                        )
                    case Edit(entries, removed, snapshot):
                        if snapshot is not None:
                            self.snapshot = snapshot
                        await self.enqueue(entries)
                        self.radio.remember_removals(removed)
                    case Stop():
                        self.radio.stop()
                    case Retry():
                        self.radio.retry(ACTOR)
                    case _:
                        await self.radio.handle(message)
                self.handled.append(message)
                self.radio.observe_playback()
                # Model the Session's post-commit change subscriptions, not a
                # task-done callback or blanket post-operation refill.
                if before != (self.snapshot, self.radio.status):
                    self.post(RefillRadio(self.radio.status.session_id))
                if not done.done():
                    done.set_result(None)
            except Exception as exc:
                if not done.done():
                    done.set_exception(exc)

    async def enqueue(self, entries: tuple[QueueEntry, ...]) -> None:
        self.snapshot = queue.enqueue(self.snapshot, entries)

    async def play(self) -> None:
        self.play_calls += 1
        self.snapshot = replace(
            self.snapshot,
            state=PlaybackState.LOADING,
            current=self.snapshot.upcoming[0],
            upcoming=self.snapshot.upcoming[1:],
        )

    async def start(self, entries: tuple[CatalogTrack, ...] = ()) -> None:
        await self.post(Start(entries))

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.radio.close()
        self.inbox.put_nowait(None)
        await self.actor


async def wait_for(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def test_refill_rechecks_manual_edits_removals_and_history_after_fetch() -> None:
    async def scenario() -> None:
        current = QueueEntry("https://youtu.be/00000000000")
        old = QueueEntry("https://youtu.be/00000000001")
        harness = RadioHarness(
            PlayerSnapshot(
                state=PlaybackState.PLAYING,
                current=current,
                recently_played=(HistoryEntry(old, datetime.now(UTC)),),
            )
        )
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            manual = QueueEntry("https://youtu.be/00000000002")
            removed = QueueEntry("https://youtu.be/00000000003")

            await harness.post(Edit((manual,), (removed,)))
            recommendations = tracks()
            harness.provider.response.set_result(
                (
                    *recommendations,
                    *recommendations,
                    replace(recommendations[0], video_id=None),
                )
            )
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            assert harness.snapshot.upcoming[0] == manual
            suggested = harness.snapshot.upcoming[1:]
            assert [item.video_id for item in suggested] == [
                "00000000004",
                "00000000005",
            ]
            assert all(
                item.origin == "radio" and item.added_by == ACTOR for item in suggested
            )
            assert harness.snapshot.current == current
            assert harness.play_calls == 0
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["stop", "switch", "close"])
def test_cancelled_fetch_cannot_repopulate_a_stopped_or_replaced_session(
    action: str,
) -> None:
    async def scenario() -> None:
        harness = RadioHarness()
        harness.provider.late = True
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            if action == "close":
                await harness.close()
            elif action == "switch":
                await harness.start(tracks(20))
            else:
                await harness.post(Stop())
            await asyncio.wait_for(harness.provider.returned.wait(), 2)
            if not harness.closed:
                await harness.send(RefillRadio(harness.radio.status.session_id))
            if action == "switch":
                await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
                assert [item.video_id for item in harness.snapshot.upcoming] == [
                    "00000000020",
                    "00000000021",
                    "00000000022",
                ]
            else:
                assert harness.snapshot.upcoming == ()
                assert harness.radio.status.state is RadioState.OFF
            assert harness.play_calls == 0
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_provider_failure_waits_without_touching_current_playback_or_busy_refilling() -> (
    None
):
    async def scenario() -> None:
        original = PlayerSnapshot(
            state=PlaybackState.PLAYING,
            current=QueueEntry("https://youtu.be/00000000000"),
        )
        harness = RadioHarness(original)
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            harness.provider.response.set_exception(
                RuntimeError("Provider unavailable")
            )
            await wait_for(lambda: harness.radio.status.state is RadioState.WAITING)
            await harness.send(RefillRadio(harness.radio.status.session_id))
            assert harness.snapshot == original
            assert harness.provider.calls == 1
            assert harness.play_calls == 0
            assert harness.radio.status.error is not None
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_committed_partial_result_refills_before_previous_sender_finishes() -> None:
    async def scenario() -> None:
        harness = RadioHarness()
        harness.hold_loaded_ack = asyncio.Event()
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            harness.provider.response.set_result(tracks()[:1])
            # The first fetch task remains awaiting acknowledgement. The actor's
            # committed result must nevertheless allow the next refill to start.
            await wait_for(lambda: harness.provider.calls == 2)
            assert len(harness.snapshot.upcoming) == 1
            harness.hold_loaded_ack.set()
            await harness.send(RefillRadio(harness.radio.status.session_id))
            assert harness.provider.calls == 2
            harness.provider.response.set_result(tracks(10))
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            assert [entry.video_id for entry in harness.snapshot.upcoming] == [
                "00000000000",
                "00000000010",
                "00000000011",
            ]
            assert harness.radio.status.state is RadioState.ACTIVE
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_close_reaps_fetch_still_waiting_for_its_applied_result_reply() -> None:
    async def scenario() -> None:
        harness = RadioHarness()
        harness.hold_loaded_ack = asyncio.Event()
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            harness.provider.response.set_result(tracks())
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            assert harness.radio.status.state is RadioState.ACTIVE
            await asyncio.wait_for(harness.close(), 2)
            assert not harness.hold_loaded_ack.is_set()
            assert harness.actor.done()
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_paused_fetch_retains_pool_until_playback_resumes() -> None:
    async def scenario() -> None:
        current = QueueEntry("https://youtu.be/00000000099")
        playing = PlayerSnapshot(PlaybackState.PLAYING, current)
        harness = RadioHarness(playing)
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            await harness.post(
                Edit(snapshot=replace(playing, state=PlaybackState.PAUSED))
            )
            harness.provider.response.set_result(tracks())
            await wait_for(lambda: harness.radio.status.state is RadioState.ACTIVE)
            assert harness.snapshot.upcoming == ()
            assert harness.play_calls == 0
            await harness.post(Edit(snapshot=playing))
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            assert harness.provider.calls == 1
            assert harness.snapshot.current == current
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("connected", [True, False])
def test_fetch_continues_finished_playback_only_while_connected(
    connected: bool,
) -> None:
    async def scenario() -> None:
        current = QueueEntry("https://youtu.be/00000000099")
        harness = RadioHarness(PlayerSnapshot(PlaybackState.PLAYING, current))
        harness.connected = connected
        try:
            await harness.start()
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            await harness.post(Edit(snapshot=PlayerSnapshot()))
            harness.provider.response.set_result(tracks())
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            assert harness.play_calls == int(connected)
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_retry_fetches_once_and_late_refill_for_previous_session_is_ignored() -> None:
    async def scenario() -> None:
        harness = RadioHarness()
        try:
            await harness.start()
            previous_session = harness.radio.status.session_id
            await asyncio.wait_for(harness.provider.started.wait(), 2)
            harness.provider.response.set_exception(RuntimeError("Unavailable"))
            await wait_for(lambda: harness.radio.status.state is RadioState.WAITING)
            await harness.post(Retry())
            await wait_for(lambda: harness.provider.calls == 2)
            await harness.send(RefillRadio(harness.radio.status.session_id))
            assert harness.provider.calls == 2
            harness.provider.response.set_result(tracks())
            await wait_for(lambda: len(harness.snapshot.upcoming) == 3)
            await harness.start(tracks(20))
            await harness.send(RefillRadio(previous_session))
            assert harness.provider.calls == 2
            assert len(harness.snapshot.upcoming) == 3
        finally:
            await harness.close()

    asyncio.run(scenario())

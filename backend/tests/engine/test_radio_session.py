# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.domain.catalog import (
    MediaReference,
    TrackFinding,
    TrackPage,
)
from nahormaar_backend.engine.domain.queue import Add, Clear, QueueOrigin, Remove
from nahormaar_backend.engine.domain.radio import (
    ManualStrategy,
    RadioLoaded,
    RadioState,
    RadioStrategy,
    RetryRadio,
    StartRadio,
    StopRadio,
)
from nahormaar_backend.engine.domain.tracks import Track
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.session import Session
from nahormaar_backend.engine.youtube import YouTubeMusicProvider

from .database import isolated_database
from .test_catalog import TIME, REFERENCE
from .test_session import ACTOR, tracks_in


class ControlledMusic(YouTubeMusicProvider):
    def __init__(self, tracks: tuple[Track, ...]) -> None:
        super().__init__(Path("unused-node"))
        self.tracks = tracks
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.calls = 0
        self.concurrent = 0
        self.maximum = 0

    async def radio_next(
        self, seed: MediaReference, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        self.calls += 1
        self.concurrent += 1
        self.maximum = max(self.maximum, self.concurrent)
        self.started.set()
        try:
            await self.release.wait()
            return TrackPage(
                tuple(
                    TrackFinding(
                        MediaReference(
                            track.identity, REFERENCE.kind, track.source_url
                        ),
                        track.metadata,
                    )
                    for track in self.tracks
                )
            )
        finally:
            self.concurrent -= 1
            self.cleaned.set()


async def until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


def test_radio_rechecks_capacity_and_manual_priority_after_discovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(tracks[:6])
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            owner = await Session.open(
                sessions, uuid4(), clock=lambda: TIME, catalog=catalog
            )
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await provider.started.wait()
            # The inbox is free while the provider is blocked.
            manual = await owner.request(
                uuid4(), Add((tracks[0].id, tracks[7].id)), actor=ACTOR
            )
            provider.release.set()
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            entries = owner.snapshot.queue.entries
            assert entries[:2] == manual.outcome.entries
            assert (
                entries[2].origin is QueueOrigin.RADIO
                and entries[2].track_id == tracks[1].id
            )
            assert provider.calls == provider.maximum == 1
            await owner.request(uuid4(), Remove(entries[2].id), actor=ACTOR)
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            assert owner.snapshot.queue.entries[-1].track_id == tracks[2].id
            assert provider.calls == 1  # Refill consumes the remaining pool.
            await owner.request(
                uuid4(), Clear(owner.snapshot.settings.queue_revision), actor=ACTOR
            )
            assert not owner.snapshot.queue.entries
            assert isinstance(owner.snapshot.strategy, ManualStrategy)
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_stop_or_seed_switch_invalidates_old_results_and_closes_discovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(tracks[:6])
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            owner = await Session.open(
                sessions, uuid4(), clock=lambda: TIME, catalog=catalog
            )
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await provider.started.wait()
            original = owner.snapshot.strategy
            assert (
                isinstance(original, RadioStrategy) and original.request_id is not None
            )
            seed = MediaReference(
                tracks[0].identity, REFERENCE.kind, tracks[0].source_url
            )
            await owner.request(
                uuid4(), StartRadio(seed, original.generation), actor=ACTOR
            )
            await until(lambda: provider.calls == 2)
            assert provider.maximum == 1
            stale = await owner._submit(  # pyright: ignore[reportPrivateUsage]
                RadioLoaded(original.generation, original.request_id, (tracks[0].id,))
            )
            assert not stale.snapshot.queue.entries
            current = owner.snapshot.strategy
            assert isinstance(current, RadioStrategy)
            conflict = await owner.request(
                uuid4(), StopRadio(original.generation), actor=ACTOR
            )
            assert conflict.outcome.code == "radio_conflict"
            await owner.request(uuid4(), StopRadio(current.generation), actor=ACTOR)
            await until(lambda: provider.concurrent == 0)
            assert isinstance(owner.snapshot.strategy, ManualStrategy)
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_exhausted_radio_waits_for_explicit_retry_instead_of_looping(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(())
            provider.release.set()
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            owner = await Session.open(
                sessions, uuid4(), clock=lambda: TIME, catalog=catalog
            )
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await until(
                lambda: (
                    isinstance(owner.snapshot.strategy, RadioStrategy)
                    and owner.snapshot.strategy.state is RadioState.WAITING
                )
            )
            strategy = owner.snapshot.strategy
            assert isinstance(strategy, RadioStrategy)
            assert provider.calls == 1 and strategy.error
            # Unrelated queue changes do not turn failure into immediate network retries.
            await owner.request(uuid4(), Add((tracks[0].id,)), actor=ACTOR)
            assert provider.calls == 1
            provider.tracks = tracks[:5]
            await owner.request(uuid4(), RetryRadio(strategy.generation), actor=ACTOR)
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            assert provider.calls == 2
            assert len({entry.track_id for entry in owner.snapshot.queue.entries}) == 3
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_queue_can_be_full_before_radio_result_and_closed_session_cancels_fetch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(tracks[:5])
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            owner = await Session.open(
                sessions, uuid4(), clock=lambda: TIME, catalog=catalog
            )
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await provider.started.wait()
            await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[4:])), actor=ACTOR
            )
            provider.release.set()
            await until(
                lambda: (
                    isinstance(owner.snapshot.strategy, RadioStrategy)
                    and owner.snapshot.strategy.state is RadioState.ACTIVE
                )
            )
            assert len(owner.snapshot.queue.entries) == 4
            assert all(
                entry.origin is QueueOrigin.MANUAL
                for entry in owner.snapshot.queue.entries
            )
            await owner.request(
                uuid4(), Clear(owner.snapshot.settings.queue_revision), actor=ACTOR
            )
            provider.release.clear()
            provider.started.clear()
            provider.cleaned.clear()
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await provider.started.wait()
            await owner.close()
            assert provider.cleaned.is_set() and provider.concurrent == 0
            assert not owner.snapshot.queue.entries
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_radio_reopens_with_seed_initiator_and_remaining_pool(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio-restart.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(tracks[:6])
            provider.release.set()
            catalog = Catalog(
                (provider,), MetadataStore(sessions, clock=lambda: TIME), clock=lambda: TIME
            )
            identifier = uuid4()
            owner = await Session.open(sessions, identifier, clock=lambda: TIME, catalog=catalog)
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            original = owner.snapshot.strategy
            entries = owner.snapshot.queue.entries
            assert isinstance(original, RadioStrategy)
            assert provider.calls == 1
            await owner.close()

            owner = await Session.open(sessions, identifier, clock=lambda: TIME, catalog=catalog)
            assert owner.snapshot.strategy == original
            assert owner.snapshot.queue.entries == entries
            assert provider.calls == 1
            await owner.request(uuid4(), Remove(entries[0].id), actor=ACTOR)
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            assert owner.snapshot.queue.entries[-1].track_id == tracks[3].id
            assert provider.calls == 1
            restored = owner.snapshot.strategy
            assert isinstance(restored, RadioStrategy)
            assert restored.initiator == ACTOR
            await owner.request(uuid4(), StopRadio(original.generation), actor=ACTOR)
            await owner.close()

            owner = await Session.open(sessions, identifier, clock=lambda: TIME, catalog=catalog)
            assert isinstance(owner.snapshot.strategy, ManualStrategy)
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_restart_retries_only_the_cancelled_radio_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "radio-loading.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = ControlledMusic(tracks[:5])
            catalog = Catalog(
                (provider,), MetadataStore(sessions, clock=lambda: TIME), clock=lambda: TIME
            )
            identifier = uuid4()
            owner = await Session.open(sessions, identifier, clock=lambda: TIME, catalog=catalog)
            await owner.request(uuid4(), StartRadio(REFERENCE, None), actor=ACTOR)
            await provider.started.wait()
            loading = owner.snapshot.strategy
            assert isinstance(loading, RadioStrategy)
            assert loading.state is RadioState.LOADING
            await owner.close()
            assert provider.cleaned.is_set()

            provider.release.set()
            owner = await Session.open(sessions, identifier, clock=lambda: TIME, catalog=catalog)
            await until(lambda: len(owner.snapshot.queue.entries) == 3)
            assert provider.calls == 2
            restored = owner.snapshot.strategy
            assert isinstance(restored, RadioStrategy)
            assert restored.generation == loading.generation
            assert restored.initiator == ACTOR
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())

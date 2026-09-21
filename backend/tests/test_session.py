# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from nahormaar_backend.application.catalog import MediaCatalog
from nahormaar_backend.application.radio import RadioCatalog, RadioPreview, RefillRadio
from nahormaar_backend.application.session import Session, SessionManager
from nahormaar_backend.application.storage import PlayerStore, StorageError
from nahormaar_backend.domain.catalog import CatalogTrack
from nahormaar_backend.domain.models import (
    Contributor,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from nahormaar_backend.domain.radio import RadioSeed
from nahormaar_backend.integrations.processes import ProcessResult
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import ControlledResolver, FakeVoice


class OwnedVoice(FakeVoice):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        await self.disconnect()


class OwnedStore(SQLiteStore):
    def __init__(self, path: Path) -> None:
        self.close_count = 0
        super().__init__(path)

    def close(self) -> None:
        self.close_count += 1
        super().close()


def test_session_composes_fake_services_and_one_authoritative_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        voice, resolver = OwnedVoice(), ControlledResolver()
        session = await Session.create(
            lambda: SQLiteStore(tmp_path / "session.sqlite3"), resolver, voice
        )
        try:
            assert session.voice is voice
            assert session.resolver is resolver
            assert session.snapshot == PlayerSnapshot()
            async with session.subscribe() as events:
                assert await events.get() == await session.read_status()
                entry = QueueEntry("https://youtu.be/abcdefghijk")
                await session.enqueue(entry)
                status = await events.get()
                assert status is not None
                assert status.player == session.snapshot
                assert session.snapshot.upcoming == (entry,)
                assert (
                    await session.state.worker.call(lambda p: p.snapshot)
                    == session.snapshot
                )
                assert status.revision == session.state.revisions.revision
        finally:
            await session.close()
        assert voice.close_count == 1
        assert resolver.calls == []
        with pytest.raises(RuntimeError, match="closed"):
            await session.read_status()

    asyncio.run(scenario())


def test_manager_creates_only_one_session_and_closes_it_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = SessionManager()
        voice, resolver = OwnedVoice(), ControlledResolver()
        stores: list[OwnedStore] = []

        def factory() -> PlayerStore:
            store = OwnedStore(tmp_path / "manager.sqlite3")
            stores.append(store)
            return store

        session = await manager.create(factory, resolver, voice)
        try:
            with pytest.raises(RuntimeError, match="already been created"):
                await manager.create(factory, resolver, voice)
        finally:
            await asyncio.gather(manager.close(), manager.close())
        await manager.close()
        assert len(stores) == 1
        assert stores[0].close_count == voice.close_count == 1
        with pytest.raises(RuntimeError, match="closed"):
            await session.state.worker.call(lambda p: p.snapshot)

    asyncio.run(scenario())


def test_session_factory_failure_closes_owned_voice() -> None:
    async def scenario() -> None:
        voice = OwnedVoice()

        def factory() -> PlayerStore:
            raise StorageError("Factory failed")

        with pytest.raises(StorageError, match="Factory failed"):
            await Session.create(factory, ControlledResolver(), voice)
        assert voice.close_count == 1

    asyncio.run(scenario())


def test_session_load_failure_closes_allocated_store_and_voice(tmp_path: Path) -> None:
    class BrokenStore(OwnedStore):
        loads = 0

        def load(self) -> PlayerSnapshot:
            self.loads += 1
            if self.loads == 2:
                raise StorageError("Load failed")
            return super().load()

    async def scenario() -> None:
        voice = OwnedVoice()
        stores: list[BrokenStore] = []

        def factory() -> PlayerStore:
            store = BrokenStore(tmp_path / "broken.sqlite3")
            stores.append(store)
            return store

        with pytest.raises(StorageError, match="Load failed"):
            await Session.create(factory, ControlledResolver(), voice)
        assert len(stores) == 1
        assert stores[0].close_count == voice.close_count == 1

    asyncio.run(scenario())


def test_cancelled_startup_closes_store_after_factory_finishes(tmp_path: Path) -> None:
    factory_started, release_factory = Event(), Event()
    stores: list[OwnedStore] = []

    class StartupVoice(OwnedVoice):
        async def close(self) -> None:
            release_factory.set()
            await super().close()

    def factory() -> PlayerStore:
        factory_started.set()
        assert release_factory.wait(2)
        store = OwnedStore(tmp_path / "cancelled.sqlite3")
        stores.append(store)
        return store

    async def scenario() -> None:
        voice = StartupVoice()
        creating = asyncio.create_task(
            Session.create(factory, ControlledResolver(), voice)
        )
        try:
            assert await asyncio.to_thread(factory_started.wait, 2)
            creating.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(creating, 2)
            assert len(stores) == 1
            assert stores[0].close_count == voice.close_count == 1
        finally:
            release_factory.set()
            if not creating.done():
                creating.cancel()
            await asyncio.gather(creating, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["halt", "voice"])
def test_close_failure_still_reaps_other_owned_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        voice = OwnedVoice()
        stores: list[OwnedStore] = []

        def factory() -> PlayerStore:
            store = OwnedStore(tmp_path / "cleanup.sqlite3")
            stores.append(store)
            return store

        session = await Session.create(factory, ControlledResolver(), voice)
        target = session.playback.halt if failure == "halt" else voice.close

        async def fail_close() -> None:
            await target()
            raise RuntimeError(f"{failure} cleanup failed")

        monkeypatch.setattr(
            session.playback if failure == "halt" else voice,
            "halt" if failure == "halt" else "close",
            fail_close,
        )
        for _ in range(2):
            with pytest.raises(ExceptionGroup, match="shutdown failed") as raised:
                await session.close()
            assert [str(error) for error in raised.value.exceptions] == [
                f"{failure} cleanup failed"
            ]
        assert voice.close_count == stores[0].close_count == 1
        with pytest.raises(RuntimeError, match="closed"):
            await session.state.worker.call(lambda p: p.snapshot)

    asyncio.run(scenario())


class SharedExtractor:
    def __init__(self) -> None:
        self.close_count = 0
        self.stop_count = 0

    async def run(
        self,
        source: str,
        options: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        raise AssertionError("Session must not start discovery by construction.")

    def stop_accepting(self) -> None:
        self.stop_count += 1

    async def close(self) -> None:
        self.close_count += 1


class EmptyRecommendations:
    async def recommend(self, seed: RadioSeed, limit: int) -> tuple[CatalogTrack, ...]:
        return ()


class SharedRadioCatalog(RadioCatalog):
    def __init__(self) -> None:
        super().__init__(EmptyRecommendations())
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


def test_session_does_not_close_runtime_owned_catalogs(tmp_path: Path) -> None:
    async def scenario() -> None:
        extractor = SharedExtractor()
        catalog, radio = MediaCatalog(extractor, {}), SharedRadioCatalog()
        try:
            session = await Session.create(
                lambda: SQLiteStore(tmp_path / "shared.sqlite3"),
                ControlledResolver(),
                OwnedVoice(),
                catalog=catalog,
                radio_catalog=radio,
            )
            await session.close()
            assert (
                extractor.stop_count == extractor.close_count == radio.close_count == 0
            )
            # Shared services are still usable by their owner after the Session ends.
            owner, preview_id = uuid4(), uuid4()
            preview = await radio.preview(
                preview_id, owner, RadioSeed("track", "abcdefghijk", "Seed")
            )
            assert radio.get(preview_id, owner) == preview
        finally:
            await catalog.close()
            await radio.close()
        assert extractor.close_count == radio.close_count == 1

    asyncio.run(scenario())


def test_late_metadata_cannot_mutate_a_closed_session(tmp_path: Path) -> None:
    class LateMetadata:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def metadata(self, source_url: str) -> TrackMetadata:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
            return TrackMetadata(title="Late title", duration_seconds=90)

    async def scenario() -> None:
        metadata = LateMetadata()
        database = tmp_path / "metadata.sqlite3"
        session = await Session.create(
            lambda: SQLiteStore(database),
            ControlledResolver(),
            OwnedVoice(),
            metadata_resolver=metadata,
        )
        entry = QueueEntry("https://youtu.be/abcdefghijk")
        try:
            await session.enqueue(entry)
            await asyncio.wait_for(metadata.started.wait(), 2)
        finally:
            await asyncio.wait_for(session.close(), 2)
        assert metadata.cancelled
        assert session.snapshot.upcoming == (entry,)
        with SQLiteStore(database) as store:
            assert store.load().upcoming == (entry,)

    asyncio.run(scenario())


def test_late_radio_result_cannot_mutate_a_closed_session(tmp_path: Path) -> None:
    class LateRecommendations:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False

        async def recommend(
            self, seed: RadioSeed, limit: int
        ) -> tuple[CatalogTrack, ...]:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
            return (
                CatalogTrack(
                    video_id="abcdefghijk",
                    source_url="https://youtu.be/abcdefghijk",
                    title="Late recommendation",
                ),
            )

    async def scenario() -> None:
        provider = LateRecommendations()
        radio = RadioCatalog(provider)
        database = tmp_path / "radio.sqlite3"
        session = await Session.create(
            lambda: SQLiteStore(database),
            ControlledResolver(),
            OwnedVoice(),
            radio_catalog=radio,
        )
        try:
            session.radio.start(
                RadioPreview(uuid4(), RadioSeed("track", "lmnopqrstuv", "Seed"), ()),
                Contributor(uuid4(), "Listener", "0001"),
            )
            await session._send(  # pyright: ignore[reportPrivateUsage]
                RefillRadio(session.radio.status.session_id)
            )
            await asyncio.wait_for(provider.started.wait(), 2)
        finally:
            await asyncio.wait_for(session.close(), 2)
            await radio.close()
        assert provider.cancelled
        assert session.snapshot.upcoming == ()
        with SQLiteStore(database) as store:
            assert store.load().upcoming == ()

    asyncio.run(scenario())

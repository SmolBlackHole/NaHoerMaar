# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.application.radio import RadioCatalog
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.catalog import CatalogTrack
from nahormaar_backend.domain.models import Contributor, PlaybackState, QueueEntry
from nahormaar_backend.domain.radio import (
    RadioEvent,
    RadioSeed,
    RadioState,
    radio_transition,
)
from nahormaar_backend.integrations.processes import ProcessResult
from nahormaar_backend.integrations.youtube_radio import YouTubeMusicRadio, radio_seed
from nahormaar_backend.persistence.player_store import SQLiteStore, StorageError
from test_commands import wait_for
from test_playback import ControlledResolver, FakeVoice

ACTOR = Contributor(uuid4(), "Andrey", "0002")
SEED = RadioSeed("track", "Pqp9fDRp1lw", "Hazy Mercer")


def tracks(count: int = 12, start: int = 0) -> tuple[CatalogTrack, ...]:
    return tuple(
        CatalogTrack(
            index=i,
            video_id=f"{i:011}",
            source_url=f"https://music.youtube.com/watch?v={i:011}",
            title=f"Song {i}",
            artist="Artist",
            duration_seconds=180,
        )
        for i in range(start, start + count)
    )


class Recommendations:
    def __init__(self) -> None:
        self.entries = tracks()
        self.gate: asyncio.Future[tuple[CatalogTrack, ...]] | None = None
        self.calls = 0
        self.late = False

    async def recommend(self, seed: RadioSeed, limit: int) -> tuple[CatalogTrack, ...]:
        self.calls += 1
        if self.gate is not None:
            try:
                return await asyncio.shield(self.gate)
            except asyncio.CancelledError:
                if self.late:
                    return self.entries
                raise
        return self.entries


async def setup(
    path: Path,
) -> tuple[PlaybackController, Recommendations, FakeVoice, ControlledResolver]:
    provider, voice, resolver = Recommendations(), FakeVoice(), ControlledResolver()
    controller = await PlaybackController.create(
        path, resolver, voice, radio_catalog=RadioCatalog(provider)
    )
    return controller, provider, voice, resolver


async def start(controller: PlaybackController) -> tuple[UUID, commands.StartRadio]:
    assert controller.radio_catalog is not None
    preview = await controller.radio_catalog.preview(uuid4(), ACTOR.id, SEED)
    request = uuid4()
    command = commands.StartRadio(preview.id, controller.status.radio.session_id)
    result = await controller.request(request, command, actor_id=ACTOR.id, actor=ACTOR)
    assert result.outcome.code == "ok"
    return request, command


def test_radio_priority_refill_attribution_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "radio.sqlite3"
        controller, _, voice, resolver = await setup(path)
        try:
            request, command = await start(controller)
            await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
            assert not voice.played and controller.snapshot.current is None
            assert all(
                entry.origin == "radio" and entry.added_by == ACTOR
                for entry in controller.snapshot.upcoming
            )
            replay = await controller.request(
                request, command, actor_id=ACTOR.id, actor=ACTOR
            )
            assert replay.replayed and len(controller.snapshot.upcoming) == 3
            manual = QueueEntry("https://youtu.be/bWHJbIm1TAA", title="Manual")
            await controller.enqueue(manual)
            assert controller.snapshot.upcoming[0] == manual
            moved = controller.snapshot.upcoming[-1]
            await controller.move_before(moved.id, manual.id)
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(lambda: controller.snapshot.state is PlaybackState.PLAYING)
            assert controller.snapshot.current == moved
            assert controller.snapshot.recently_played[0].entry.origin == "radio"
            assert controller.snapshot.upcoming[0] == manual
            voice.complete(0)
            await wait_for(
                lambda: (
                    controller.snapshot.current == manual
                    and len(controller.snapshot.upcoming) == 3
                )
            )
        finally:
            await controller.close()
        restored, _, _, _ = await setup(path)
        try:
            assert restored.status.radio.state is RadioState.OFF
            assert restored.snapshot.state is PlaybackState.LOADING
            assert restored.snapshot.current == manual
            assert restored.snapshot.recently_played[0].entry.origin == "radio"
            assert len(restored.snapshot.upcoming) == 3
        finally:
            await restored.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["stop", "clear", "disconnect", "radio", "switch"])
def test_late_radio_results_cannot_revive_stopped_session(
    tmp_path: Path, action: str
) -> None:
    async def scenario() -> None:
        controller, provider, _, _ = await setup(tmp_path / "radio.sqlite3")
        provider.entries = ()
        try:
            # Preview completes, subsequent refill is held while the user acts.
            assert controller.radio_catalog is not None
            preview = await controller.radio_catalog.preview(uuid4(), ACTOR.id, SEED)
            replacement = await controller.radio_catalog.preview(
                uuid4(), ACTOR.id, replace(SEED, title="Other radio")
            )
            provider.gate = asyncio.get_running_loop().create_future()
            provider.entries = tracks()
            provider.late = True
            await controller.request(
                uuid4(),
                commands.StartRadio(preview.id, None),
                actor_id=ACTOR.id,
                actor=ACTOR,
            )
            await wait_for(lambda: provider.calls == 3)
            session = controller.status.radio.session_id
            assert session is not None
            if action == "radio":
                await controller.request(
                    uuid4(), commands.StopRadio(session), actor_id=ACTOR.id, actor=ACTOR
                )
            elif action == "switch":
                await controller.request(
                    uuid4(),
                    commands.StartRadio(replacement.id, session),
                    actor_id=ACTOR.id,
                    actor=ACTOR,
                )
                await wait_for(lambda: provider.calls == 4)
                assert controller.status.radio.session_id != session
            else:
                await getattr(
                    controller,
                    {"disconnect": "disconnect", "stop": "stop", "clear": "clear"}[
                        action
                    ],
                )()
            await asyncio.sleep(0.03)
            assert controller.snapshot.upcoming == ()
            if action != "switch":
                assert controller.status.radio.state is RadioState.OFF
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_removal_undo_pause_and_history_exclusions(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, _, _, resolver = await setup(tmp_path / "radio.sqlite3")
        try:
            await start(controller)
            await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(
                lambda: (
                    controller.snapshot.state is PlaybackState.PLAYING
                    and len(controller.snapshot.upcoming) == 3
                )
            )
            await controller.pause()
            removed = controller.snapshot.upcoming[0]
            result = await controller.request(
                uuid4(), commands.Remove(removed.id), actor_id=ACTOR.id, actor=ACTOR
            )
            await asyncio.sleep(0.02)
            assert len(controller.snapshot.upcoming) == 2
            assert result.outcome.undo_id
            await controller.request(
                uuid4(),
                commands.Undo(result.outcome.undo_id),
                actor_id=ACTOR.id,
                actor=ACTOR,
            )
            assert removed in controller.snapshot.upcoming
            await controller.remove(removed.id)
            await controller.play()
            await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
            assert removed.video_id not in {
                entry.video_id for entry in controller.snapshot.upcoming
            }
            assert controller.snapshot.current is not None
            assert controller.snapshot.current.video_id not in {
                entry.video_id for entry in controller.snapshot.upcoming
            }
            state = await controller.read_status()
            result = await controller.request(
                uuid4(),
                commands.Clear(state.queue_revision),
                actor_id=ACTOR.id,
                actor=ACTOR,
            )
            assert controller.status.radio.state is RadioState.OFF
            assert controller.snapshot.current is not None
            assert result.outcome.undo_id
            await controller.request(
                uuid4(),
                commands.Undo(result.outcome.undo_id),
                actor_id=ACTOR.id,
                actor=ACTOR,
            )
            assert len(controller.snapshot.upcoming) == 3
            assert controller.status.radio.state is RadioState.OFF
            assert all(
                entry.origin == "radio"
                for entry in (await controller.read_status()).player.upcoming
            )
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_empty_provider_waits_and_retries_without_busy_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller, provider, _, _ = await setup(tmp_path / "radio.sqlite3")
        provider.entries = ()
        try:
            await start(controller)
            await wait_for(lambda: controller.status.radio.state is RadioState.WAITING)
            calls = provider.calls
            await asyncio.sleep(0.03)
            assert provider.calls == calls
            provider.entries = tracks()
            session = controller.status.radio.session_id
            assert session
            result = await controller.request(
                uuid4(), commands.RetryRadio(session), actor_id=ACTOR.id, actor=ACTOR
            )
            assert result.outcome.code == "ok"
            await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
            assert controller.status.radio.error is None
        finally:
            await controller.close()

    asyncio.run(scenario())


def test_failed_save_never_publishes_radio_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        controller, provider, _, _ = await setup(tmp_path / "radio.sqlite3")
        provider.entries = ()
        try:
            await start(controller)
            await wait_for(lambda: controller.status.radio.state is RadioState.WAITING)
            saved = SQLiteStore.save

            def fail(self: SQLiteStore, snapshot: object, **kwargs: object) -> None:
                raise StorageError("Simulated failure")

            monkeypatch.setattr(SQLiteStore, "save", fail)
            session = controller.status.radio.session_id
            assert session
            with pytest.raises(StorageError):
                await controller.request(
                    uuid4(),
                    commands.RetryRadio(session),
                    actor_id=ACTOR.id,
                    actor=ACTOR,
                )
            assert controller.status.radio.state is RadioState.OFF
            assert controller.snapshot.upcoming == ()
            monkeypatch.setattr(SQLiteStore, "save", saved)
        finally:
            await controller.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("during_fetch", ["pause", "manual", "storage_failure"])
def test_pending_refill_respects_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during_fetch: str
) -> None:
    async def scenario() -> None:
        controller, provider, _, resolver = await setup(tmp_path / "radio.sqlite3")
        saved = SQLiteStore.save
        try:
            await controller.enqueue(QueueEntry("https://youtu.be/bWHJbIm1TAA"))
            await controller.connect(7)
            await controller.play()
            await wait_for(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await wait_for(lambda: controller.snapshot.state is PlaybackState.PLAYING)
            provider.entries = ()
            assert controller.radio_catalog is not None
            preview = await controller.radio_catalog.preview(uuid4(), ACTOR.id, SEED)
            provider.gate = asyncio.get_running_loop().create_future()
            await controller.request(
                uuid4(),
                commands.StartRadio(preview.id, None),
                actor_id=ACTOR.id,
                actor=ACTOR,
            )
            await wait_for(lambda: provider.calls == 2)
            manual = QueueEntry("https://youtu.be/00000000000", title="My request")
            if during_fetch == "pause":
                await controller.pause()
            elif during_fetch == "manual":
                await controller.enqueue(manual)
            else:

                def fail(self: SQLiteStore, snapshot: object, **kwargs: object) -> None:
                    raise StorageError("Simulated refill write failure")

                monkeypatch.setattr(SQLiteStore, "save", fail)
            provider.gate.set_result(tracks())
            await wait_for(
                lambda: controller.status.radio.state is not RadioState.LOADING
            )
            if during_fetch == "pause":
                assert controller.snapshot.upcoming == ()
                await controller.play()
                await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
                assert provider.calls == 2
            elif during_fetch == "manual":
                assert len(controller.snapshot.upcoming) == 3
                assert controller.snapshot.upcoming[0] == manual
                assert (
                    len(
                        {
                            entry.video_id or entry.source_url
                            for entry in controller.snapshot.upcoming
                        }
                    )
                    == 3
                )
                assert (
                    sum(
                        entry.source_url.endswith("00000000000")
                        for entry in controller.snapshot.upcoming
                    )
                    == 1
                )
            else:
                assert controller.status.radio.state is RadioState.OFF
                assert controller.snapshot.upcoming == ()
        finally:
            monkeypatch.setattr(SQLiteStore, "save", saved)
            await controller.close()

    asyncio.run(scenario())


def test_preview_ownership_and_limits() -> None:
    async def scenario() -> None:
        provider = Recommendations()
        provider.entries = tracks(80) + tracks()
        catalog = RadioCatalog(provider)
        try:
            identifier = uuid4()
            first, replay = await asyncio.gather(
                catalog.preview(identifier, ACTOR.id, SEED),
                catalog.preview(identifier, ACTOR.id, SEED),
            )
            assert first == replay and len(first.entries) == 25 and provider.calls == 1
            with pytest.raises(ValueError):
                catalog.get(identifier, uuid4())
            with pytest.raises(ValueError):
                await catalog.preview(
                    identifier, ACTOR.id, replace(SEED, title="Changed")
                )
        finally:
            await catalog.close()

    asyncio.run(scenario())


def test_watch_metadata_and_playlist_seed() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, ...]] = []

        async def run(args: tuple[str, ...]) -> ProcessResult:
            calls.append(args)
            return ProcessResult(
                0,
                b'{"tracks":[{"videoId":"Pqp9fDRp1lw","title":"Song","length":"1:02:03","thumbnail":[{"url":"https://i.ytimg.com/test.jpg","width":100}],"artists":[{"name":"Artist","id":"UCtest"}]}]}',
                b"",
            )

        seed = radio_seed(
            "https://music.youtube.com/playlist?list=PLtest123456", "playlist", "Mix"
        )
        items = await YouTubeMusicRadio(run).recommend(seed, 25)
        assert calls[0][-3:] == ("playlist", "PLtest123456", "25")
        assert items[0].duration_seconds == 3723
        assert (
            items[0].artist == "Artist"
            and items[0].thumbnail_url == "https://i.ytimg.com/test.jpg"
        )

    asyncio.run(scenario())


def test_fsm_rejects_invalid_retry() -> None:
    with pytest.raises(ValueError):
        radio_transition(RadioState.OFF, RadioEvent.RETRY)

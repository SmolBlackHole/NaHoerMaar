# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session as DatabaseSession

from nahormaar_backend.engine.audio import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioProgress,
    AudioStarted,
    CrossfadeCompleted,
    CrossfadeDue,
    PlayableSource,
    VoiceChannel,
    VoiceConnection,
    VoiceDisconnected,
)
from nahormaar_backend.engine.catalog import Catalog
from nahormaar_backend.engine.domain.catalog import MediaReference, TrackFinding
from nahormaar_backend.engine.domain.playback import Control, Join, Seek, SetCrossfade
from nahormaar_backend.engine.domain.queue import Add, Remove, Undo
from nahormaar_backend.engine.domain.sessions import PlaybackIntent, PlaybackPhase
from nahormaar_backend.engine.domain.tracks import MediaIdentity, Track
from nahormaar_backend.engine.metadata import MetadataStore
from nahormaar_backend.engine.persistence import (
    PlaybackCheckpointRepository,
    PlaybackRecordRepository,
    QueueRepository,
)
from nahormaar_backend.engine.session import Session
from nahormaar_backend.engine.youtube import YouTubeProvider

from .database import isolated_database
from .test_catalog import REFERENCE, TIME
from .test_radio_session import until
from .test_session import ACTOR, tracks_in


class SourceProvider(YouTubeProvider):
    """No processes/network: exercise real catalog lookup and metadata ingestion."""

    def __init__(self, tracks: tuple[Track, ...]) -> None:
        super().__init__(Path("unused"))
        self.tracks = {track.identity: track for track in tracks}
        self.release = asyncio.Event()
        self.release.set()
        self.requests = 0
        self.active = 0
        self.maximum = 0

    async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource:
        self.requests += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await self.release.wait()
            track = self.tracks[identity]
            return PlayableSource(
                TrackFinding(
                    MediaReference(identity, REFERENCE.kind, track.source_url),
                    replace(track.metadata, duration_seconds=120),
                ),
                "https://stream.invalid/private",
                is_opus=True,
            )
        finally:
            self.active -= 1


class ControlledOutput:
    def __init__(self) -> None:
        self.progress: AudioProgress | None = None
        self.callbacks: dict[UUID, Callable[[AudioEvent], None]] = {}
        self.started: list[UUID] = []
        self.stopped: list[UUID] = []
        self.prepared: tuple[UUID, UUID] | None = None
        self.volume = 1.0
        self.activate = True

    async def play(
        self,
        source: PlayableSource,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None:
        assert self.progress is None
        self.progress = AudioProgress(attempt_id, position_seconds, paused)
        self.callbacks[attempt_id] = notify
        self.started.append(attempt_id)

    def confirm(self, seconds: float = 0.02) -> None:
        assert self.progress
        self.progress = replace(self.progress, position_seconds=seconds)
        self.callbacks[self.progress.attempt_id](
            AudioStarted(self.progress.attempt_id, seconds)
        )

    async def stop(self, attempt_id: UUID) -> None:
        self.stopped.append(attempt_id)
        if self.progress and self.progress.attempt_id == attempt_id:
            self.callbacks[attempt_id](
                AudioCompleted(
                    attempt_id, self.progress.position_seconds, AudioEndReason.STOPPED
                )
            )
            self.progress = None
            self.prepared = None

    def pause(self, attempt_id: UUID) -> None:
        if self.progress and self.progress.attempt_id == attempt_id:
            self.progress = replace(self.progress, paused=True)

    def resume(self, attempt_id: UUID) -> None:
        if self.progress and self.progress.attempt_id == attempt_id:
            self.progress = replace(self.progress, paused=False)

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    async def prepare_next(
        self,
        source: PlayableSource,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        seconds: float,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        if not self.progress or self.progress.attempt_id != outgoing_attempt_id:
            return False
        assert self.prepared is None
        self.prepared = outgoing_attempt_id, preparation_id
        return True

    async def discard_next(self, preparation_id: UUID) -> None:
        if self.prepared and self.prepared[1] == preparation_id:
            self.prepared = None

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        if not self.activate or self.prepared != (outgoing_attempt_id, preparation_id):
            return False
        self.prepared = None
        self.progress = AudioProgress(attempt_id, 0.02, transitioning=True)
        self.callbacks[attempt_id] = notify
        notify(AudioStarted(attempt_id, 0.02))
        return True

    async def close(self) -> None:
        self.progress = None


class ControlledVoice:
    def __init__(self) -> None:
        self.connection: VoiceConnection | None = None
        self.handler: Callable[[VoiceDisconnected], None] = lambda _: None
        self.joins: list[VoiceConnection] = []

    def channels(self) -> tuple[VoiceChannel, ...]:
        return ()

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None:
        self.handler = handler

    async def connect(self, channel_id: int, connection_id: UUID) -> None:
        assert self.connection is None
        self.connection = VoiceConnection(connection_id, channel_id)
        self.joins.append(self.connection)

    async def disconnect(self, connection_id: UUID) -> None:
        if self.connection and self.connection.connection_id == connection_id:
            self.connection = None

    async def close(self) -> None:
        self.connection = None


def test_committed_lifecycle_pause_seek_and_playing_paused_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "lifecycle.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            identifier = uuid4()
            owner = await Session.open(
                sessions,
                identifier,
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[:3])), actor=ACTOR
            )
            joined = await owner.request(uuid4(), Join(123))
            assert joined.outcome.code == "ok"
            await until(lambda: audio.progress is not None)
            assert owner.snapshot.checkpoint.entry_id and not owner.snapshot.history
            async with sessions.begin() as db:
                assert (
                    await PlaybackCheckpointRepository(db).get(identifier)
                    == owner.snapshot.checkpoint
                )
                assert len(await QueueRepository(db).entries(identifier)) == 2
            audio.confirm()
            await until(lambda: len(owner.snapshot.history) == 1)
            logical_play = owner.snapshot.checkpoint.play_id
            assert logical_play
            assert audio.progress
            audio.progress = replace(audio.progress, position_seconds=25)
            pause_id = uuid4()
            await owner.request(pause_id, Control.PAUSE)
            assert (
                audio.progress.paused
                and owner.snapshot.checkpoint.position_seconds == 25
            )
            previous_attempt = audio.progress.attempt_id
            await owner.request(uuid4(), Seek(50))
            await until(
                lambda: (
                    audio.progress is not None
                    and audio.progress.attempt_id != previous_attempt
                )
            )
            assert (
                audio.progress
                and audio.progress.position_seconds == 50
                and audio.progress.paused
            )
            audio.confirm(50.02)
            await until(lambda: owner.snapshot.playback.phase is PlaybackPhase.PAUSED)
            assert (
                len(owner.snapshot.history) == 1
                and owner.snapshot.checkpoint.play_id == logical_play
            )
            for paused in (True, False):
                if not paused:
                    await owner.request(uuid4(), Control.PLAY)
                assert audio.progress
                audio.progress = replace(audio.progress, position_seconds=65)
                await owner.close()
                assert audio.progress is None and voice.connection is None
                restored_audio, restored_voice = ControlledOutput(), ControlledVoice()
                owner = await Session.open(
                    sessions,
                    identifier,
                    clock=lambda: TIME,
                    catalog=catalog,
                    audio=restored_audio,
                    voice=restored_voice,
                )
                audio, voice = restored_audio, restored_voice
                await until(lambda: restored_audio.progress is not None)  # noqa: B023 - awaited before next iteration
                assert (
                    audio.progress
                    and audio.progress.position_seconds == 65
                    and audio.progress.paused is paused
                )
                assert owner.snapshot.checkpoint.play_id == logical_play
                audio.confirm(65.02)
                await until(
                    lambda: (
                        owner.snapshot.playback.phase  # noqa: B023 - awaited in this iteration
                        is (PlaybackPhase.PAUSED if paused else PlaybackPhase.PLAYING)  # noqa: B023
                    )
                )
                assert len(owner.snapshot.history) == 1
            replay = await owner.request(pause_id, Control.PAUSE)
            assert (
                replay.replayed
                and owner.snapshot.checkpoint.intent is PlaybackIntent.PLAYING
            )
            async with sessions.begin() as db:
                assert len(await PlaybackRecordRepository(db).recent(identifier)) == 1
            await owner.close()
            await catalog.close()
            await provider.close()

    asyncio.run(scenario())


def test_lost_voice_then_old_callbacks_do_not_consume_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "loss.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[:2])), actor=ACTOR
            )
            await owner.request(uuid4(), Join(123))
            await until(lambda: audio.progress is not None)
            audio.confirm()
            await until(lambda: bool(owner.snapshot.history))
            before = owner.snapshot
            old_connection, old_progress = voice.connection, audio.progress
            assert old_connection and old_progress
            voice.connection = None
            audio.callbacks[old_progress.attempt_id](
                AudioCompleted(old_progress.attempt_id, 48, AudioEndReason.NATURAL)
            )
            await until(
                lambda: owner.snapshot.playback.phase is PlaybackPhase.SUSPENDED
            )
            assert owner.snapshot.queue == before.queue
            assert owner.snapshot.checkpoint.entry_id == before.checkpoint.entry_id
            assert owner.snapshot.checkpoint.position_seconds == 48
            await owner.request(uuid4(), Join(456))
            await until(
                lambda: (
                    audio.progress is not None
                    and audio.progress.attempt_id != old_progress.attempt_id
                )
            )
            assert audio.progress and audio.progress.position_seconds == 48
            after_join = owner.snapshot
            await owner.report(VoiceDisconnected(old_connection, old_progress))
            await owner.report(
                AudioCompleted(old_progress.attempt_id, 50, AudioEndReason.INTERRUPTED)
            )
            assert owner.snapshot == after_join
            await owner.close()
            await catalog.close()

    asyncio.run(scenario())


def test_resolution_is_cancelled_on_skip_and_no_effect_runs_before_commit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "cancel.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            provider.release.clear()
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[:3])), actor=ACTOR
            )
            await owner.request(uuid4(), Join(123))
            await until(lambda: provider.active == 1)
            before = owner.snapshot

            def fail_commit(db: DatabaseSession) -> None:
                raise RuntimeError("fixture commit failure")

            event.listen(DatabaseSession, "before_commit", fail_commit)
            try:
                with pytest.raises(RuntimeError, match="fixture"):
                    await owner.request(uuid4(), Control.SKIP)
            finally:
                event.remove(DatabaseSession, "before_commit", fail_commit)
            assert (
                owner.snapshot == before
                and provider.requests == 1
                and not audio.started
            )
            await owner.request(uuid4(), Control.SKIP)
            await until(lambda: provider.requests == 2)
            assert provider.active == provider.maximum == 1
            provider.release.set()
            await until(lambda: audio.progress is not None)
            assert owner.snapshot.checkpoint.track_id == tracks[1].id
            assert len(audio.started) == 1 and not owner.snapshot.history
            await owner.close()
            assert provider.active == 0
            await catalog.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("activate", [True, False])
def test_crossfade_confirmation_and_fallback_preserve_occurrences(
    tmp_path: Path, activate: bool
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "fade.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            audio.activate = activate
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            added = await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks[:3])), actor=ACTOR
            )
            entries = added.outcome.entries
            await owner.request(uuid4(), SetCrossfade(5))
            await owner.request(uuid4(), Join(123))
            await until(lambda: audio.progress is not None)
            audio.confirm()
            await until(lambda: audio.prepared is not None)
            assert audio.prepared
            outgoing, prepared = audio.prepared
            audio.callbacks[outgoing](CrossfadeDue(outgoing, prepared, 120))
            await until(
                lambda: (
                    audio.progress is not None and audio.progress.attempt_id != outgoing
                )
            )
            if not activate:
                audio.confirm()
            await until(lambda: len(owner.snapshot.history) == 2)
            assert owner.snapshot.checkpoint.entry_id == entries[1].id
            assert [entry.id for entry in owner.snapshot.queue.entries] == [
                entries[2].id
            ]
            if activate:
                assert audio.progress
                await owner.report(
                    CrossfadeCompleted(audio.progress.attempt_id, prepared)
                )
            assert owner.snapshot.history[1].ended_at is not None
            await owner.close()
            await catalog.close()

    asyncio.run(scenario())


def test_undo_cannot_restore_current_entry_and_stop_does_not_restart_on_boot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "undo.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            identifier = uuid4()
            owner = await Session.open(
                sessions,
                identifier,
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            added = await owner.request(uuid4(), Add((tracks[0].id,)), actor=ACTOR)
            removed = await owner.request(
                uuid4(), Remove(added.outcome.entries[0].id), actor=ACTOR
            )
            assert removed.outcome.undo_id
            await owner.request(uuid4(), Undo(removed.outcome.undo_id), actor=ACTOR)
            await owner.request(uuid4(), Join(123))
            await until(lambda: audio.progress is not None)
            second_undo = await owner.request(
                uuid4(), Undo(removed.outcome.undo_id), actor=ACTOR
            )
            assert second_undo.outcome.code == "undo_unavailable"
            await owner.request(uuid4(), Control.STOP)
            await until(lambda: audio.progress is None)
            assert len(owner.snapshot.queue.entries) == 1
            await owner.close()
            owner = await Session.open(
                sessions,
                identifier,
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await until(
                lambda: (
                    voice.connection is not None
                    and owner.snapshot.playback.joining_id is None
                )
            )
            assert audio.progress is None and len(owner.snapshot.queue.entries) == 1
            await owner.close()
            await catalog.close()

    asyncio.run(scenario())


def test_failed_final_checkpoint_still_closes_output_and_owned_tasks(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "failed-close.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await owner.request(uuid4(), Add((tracks[0].id,)), actor=ACTOR)
            await owner.request(uuid4(), Join(123))
            await until(lambda: audio.progress is not None)
            audio.confirm()
            await until(lambda: bool(owner.snapshot.history))
            assert audio.progress
            audio.progress = replace(audio.progress, position_seconds=70)

            def fail_commit(db: DatabaseSession) -> None:
                raise RuntimeError("fixture final checkpoint failure")

            event.listen(DatabaseSession, "before_commit", fail_commit)
            try:
                async with asyncio.timeout(3):
                    with pytest.raises(RuntimeError, match="checkpoint failure"):
                        await owner.close()
            finally:
                event.remove(DatabaseSession, "before_commit", fail_commit)
            assert audio.progress is None and voice.connection is None
            assert not any(
                task.get_name().startswith("engine-") for task in asyncio.all_tasks()
            )
            async with sessions.begin() as db:
                saved = await PlaybackCheckpointRepository(db).get(
                    owner.snapshot.settings.id
                )
            assert saved and saved.position_seconds < 70
            await catalog.close()

    asyncio.run(scenario())


def test_close_settles_rapid_replacements_of_pending_resolution(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with isolated_database(tmp_path / "rapid-close.sqlite3") as sessions:
            tracks = await tracks_in(sessions)
            provider = SourceProvider(tracks)
            provider.release.clear()
            catalog = Catalog(
                (provider,),
                MetadataStore(sessions, clock=lambda: TIME),
                clock=lambda: TIME,
            )
            audio, voice = ControlledOutput(), ControlledVoice()
            owner = await Session.open(
                sessions,
                uuid4(),
                clock=lambda: TIME,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            await owner.request(
                uuid4(), Add(tuple(track.id for track in tracks)), actor=ACTOR
            )
            await owner.request(uuid4(), Join(123))
            await until(lambda: provider.active == 1)
            await asyncio.gather(
                *(owner.request(uuid4(), Control.SKIP) for _ in range(5))
            )
            async with asyncio.timeout(3):
                await owner.close()
            assert not audio.started and provider.active == 0 and provider.maximum == 1
            assert owner.snapshot.checkpoint.track_id == tracks[5].id
            assert not any(
                task.get_name().startswith("engine-") for task in asyncio.all_tasks()
            )
            await catalog.close()

    asyncio.run(scenario())

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from nahoermaar.catalog.domain import (
    ObservationQuality,
    ProviderName,
    SourceAvailability,
    Track,
    TrackId,
    TrackSource,
    TrackSourceId,
)
from nahoermaar.catalog.service import ResolvedAudio
from nahoermaar.listening.service import (
    AdvancePlayback,
    BeginPlayback,
    FinishPlayback,
)
from nahoermaar.messaging import Command, Event, MessageBus, MessageContext
from nahoermaar.player.domain import (
    ListeningSession,
    ListeningSessionId,
    PlaybackCheckpoint,
    PlaybackIntent,
    PlayerState,
    Queue,
    QueueEntry,
    QueueEntryId,
    RequestOrigin,
    TrackRequest,
    TrackRequestId,
    VoiceConnectionPhase,
)
from nahoermaar.player.events import (
    CompletePlayback,
    FailPlayback,
    VoiceConnectionChanged,
)
from nahoermaar.player.playback import (
    AudioCompleted,
    AudioEndReason,
    AudioEvent,
    AudioProgress,
    AudioSourceNotReady,
    AudioStarted,
    NowPlaying,
    PlayableSource,
    PlaybackCoordinator,
    PlaybackTransport,
    VoiceChannel,
    VoiceConnection,
    VoiceDisconnected,
    VoiceError,
)
from nahoermaar.users.domain import UserId

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


class RecordingBus(MessageBus):
    __slots__ = ("commands", "events")

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[object] = []
        self.events: list[Event] = []

    async def execute[ResultT](
        self,
        command: Command[ResultT],
        context: MessageContext | None = None,
    ) -> ResultT:
        self.commands.append(command)
        return cast(ResultT, None)

    async def publish(
        self,
        event: Event,
        context: MessageContext | None = None,
    ) -> None:
        self.events.append(event)
        await super().publish(event, context)


class StaticPlayer:
    def __init__(self, state: PlayerState) -> None:
        self.state = state


class StaticCatalog:
    def __init__(self, tracks: tuple[Track, ...]) -> None:
        self._tracks = {track.id: track for track in tracks}

    async def resolve_audio(
        self,
        track_id: TrackId,
        source_id: TrackSourceId | None,
    ) -> ResolvedAudio:
        track = self._tracks[track_id]
        source = next(
            source
            for source in track.sources
            if source_id is None or source.id == source_id
        )
        return ResolvedAudio(
            track,
            source,
            f"https://stream.invalid/{source.external_id}",
            (("User-Agent", "NaHoerMaar test"),),
            True,
        )

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]:
        return {
            track_id: self._tracks[track_id]
            for track_id in track_ids
            if track_id in self._tracks
        }


class StaticListening:
    async def active_playback(
        self,
        session_id: ListeningSessionId,
        request_id: TrackRequestId,
    ) -> None:
        assert session_id
        assert request_id
        return None


class FakeTransport(PlaybackTransport):
    def __init__(
        self,
        *,
        fail_starts: bool = False,
        fail_connects: int = 0,
    ) -> None:
        self._connection: VoiceConnection | None = None
        self._progress: AudioProgress | None = None
        self._disconnect: Callable[[VoiceDisconnected], None] | None = None
        self._audience: Callable[[], None] | None = None
        self.notify: Callable[[AudioEvent], None] | None = None
        self.attempt_id: UUID | None = None
        self.play_attempts = 0
        self.connect_attempts = 0
        self.fail_starts = fail_starts
        self.fail_connects = fail_connects
        self.volume = 0.0
        self.prepared: list[tuple[PlayableSource, float]] = []
        self.presences: list[NowPlaying] = []
        self.play_ready = asyncio.Event()
        self.prepare_ready = asyncio.Event()
        self.closed = False

    @property
    def connection(self) -> VoiceConnection | None:
        return self._connection

    @property
    def progress(self) -> AudioProgress | None:
        return self._progress

    def channels(self) -> tuple[VoiceChannel, ...]:
        return ()

    def audience(self) -> tuple[()]:
        return ()

    def set_disconnect_handler(
        self,
        handler: Callable[[VoiceDisconnected], None],
    ) -> None:
        self._disconnect = handler

    def set_audience_handler(self, handler: Callable[[], None]) -> None:
        self._audience = handler

    async def connect(self, channel_id: int, connection_id: UUID) -> None:
        self.connect_attempts += 1
        if self.connect_attempts <= self.fail_connects:
            raise VoiceError("voice unavailable")
        self._connection = VoiceConnection(connection_id, channel_id)

    async def disconnect(self, connection_id: UUID) -> None:
        assert self._connection is not None
        assert self._connection.connection_id == connection_id
        self._connection = None

    async def play(
        self,
        source: PlayableSource,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None:
        self.play_attempts += 1
        if self.fail_starts:
            raise AudioSourceNotReady("no first frame")
        self.notify = notify
        self.attempt_id = attempt_id
        self._progress = AudioProgress(
            attempt_id,
            position_seconds,
            paused,
            False,
        )
        self.play_ready.set()

    async def stop(self, attempt_id: UUID) -> None:
        if self._progress is not None and self._progress.attempt_id == attempt_id:
            self._progress = None

    def pause(self, attempt_id: UUID) -> None:
        if self._progress is not None and self._progress.attempt_id == attempt_id:
            self._progress = AudioProgress(
                attempt_id,
                self._progress.position_seconds,
                True,
                self._progress.transitioning,
            )

    def resume(self, attempt_id: UUID) -> None:
        if self._progress is not None and self._progress.attempt_id == attempt_id:
            self._progress = AudioProgress(
                attempt_id,
                self._progress.position_seconds,
                False,
                self._progress.transitioning,
            )

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
        assert outgoing_attempt_id == self.attempt_id
        assert preparation_id
        _ = notify
        self.prepared.append((source, seconds))
        self.prepare_ready.set()
        return True

    async def discard_next(self, preparation_id: UUID) -> None:
        assert preparation_id

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool:
        assert outgoing_attempt_id
        assert preparation_id
        assert attempt_id
        _ = notify
        return False

    async def set_presence(self, presence: NowPlaying) -> None:
        self.presences.append(presence)

    async def close(self) -> None:
        self.closed = True

    def lose_connection(self) -> tuple[VoiceConnection, AudioProgress]:
        assert self._connection is not None
        assert self._progress is not None
        connection, progress = self._connection, self._progress
        self._connection = None
        self._progress = None
        self.play_ready = asyncio.Event()
        return connection, progress


def _track(index: int) -> Track:
    track_id = TrackId(uuid4())
    source = TrackSource(
        id=TrackSourceId(uuid4()),
        track_id=track_id,
        provider=ProviderName.YOUTUBE,
        external_id=f"video{index}",
        source_url=f"https://www.youtube.com/watch?v=video{index}",
        observed_title=f"Track {index}",
        observed_artist="Artist",
        observed_duration_seconds=180.0,
        observed_artwork_url=None,
        observed_album_title=None,
        observed_release_date=None,
        observed_isrc=None,
        uploader_name=None,
        uploader_url=None,
        quality=ObservationQuality.DETAIL,
        availability=SourceAvailability.AVAILABLE,
        first_seen_at=NOW,
        checked_at=NOW,
    )
    return Track(
        id=track_id,
        title=f"Track {index}",
        duration_seconds=180.0,
        artwork_url=None,
        album_title=None,
        release_date=None,
        isrc=None,
        created_at=NOW,
        updated_at=NOW,
        sources=(source,),
    )


def _playing_state(
    tracks: tuple[Track, ...],
) -> tuple[PlayerState, TrackRequest]:
    session_id = ListeningSessionId(uuid4())
    actor_id = UserId(uuid4())
    requests = tuple(
        TrackRequest(
            TrackRequestId(uuid4()),
            session_id,
            track.id,
            track.sources[0].id,
            NOW,
            RequestOrigin.MANUAL,
            actor_id,
        )
        for track in tracks
    )
    queued = tuple(
        QueueEntry(QueueEntryId(uuid4()), session_id, request, position)
        for position, request in enumerate(requests[1:])
    )
    queue_revision = int(bool(queued))
    session = ListeningSession(
        session_id,
        revision=2,
        queue_revision=queue_revision,
        channel_id=42,
        volume=0.6,
        crossfade_seconds=7,
        created_at=NOW,
        updated_at=NOW,
    )
    return (
        PlayerState(
            session,
            Queue(session_id, queue_revision, queued),
            PlaybackCheckpoint(
                session_id,
                PlaybackIntent.PLAYING,
                requests[0],
                0.0,
            ),
        ),
        requests[0],
    )


async def _wait_for_command(
    bus: RecordingBus,
    command_type: type[object],
) -> object:
    async def wait() -> object:
        while True:
            for command in bus.commands:
                if isinstance(command, command_type):
                    return command
            await asyncio.sleep(0)

    return await asyncio.wait_for(wait(), timeout=1)


def _coordinator(
    state: PlayerState,
    tracks: tuple[Track, ...],
    transport: FakeTransport,
    *,
    voice_retry_delays: tuple[float, ...] = (),
) -> tuple[PlaybackCoordinator, RecordingBus]:
    bus = RecordingBus()
    coordinator = PlaybackCoordinator(
        StaticPlayer(state),
        StaticCatalog(tracks),
        StaticListening(),
        bus,
        transport,
        voice_retry_delays=voice_retry_delays,
    )
    return coordinator, bus


async def _wait_for_voice_phase(
    bus: RecordingBus,
    phase: VoiceConnectionPhase,
) -> VoiceConnectionChanged:
    async def wait() -> VoiceConnectionChanged:
        while True:
            for event in bus.events:
                if (
                    isinstance(event, VoiceConnectionChanged)
                    and event.connection.phase is phase
                ):
                    return event
            await asyncio.sleep(0)

    return await asyncio.wait_for(wait(), timeout=1)


def test_audio_facts_start_on_first_frame_and_next_track_is_preloaded() -> None:
    async def scenario() -> None:
        tracks = (_track(1), _track(2))
        state, current_request = _playing_state(tracks)
        transport = FakeTransport()
        coordinator, bus = _coordinator(state, tracks, transport)
        try:
            await coordinator.start()
            await asyncio.wait_for(transport.play_ready.wait(), timeout=1)
            assert transport.connection is not None
            assert transport.connection.channel_id == 42
            assert transport.volume == 0.6
            assert not any(
                isinstance(command, BeginPlayback) for command in bus.commands
            )

            assert transport.notify is not None
            assert transport.attempt_id is not None
            transport.notify(AudioStarted(transport.attempt_id, 0.0))
            begin = await _wait_for_command(bus, BeginPlayback)
            assert isinstance(begin, BeginPlayback)
            assert begin.request_id == current_request.id

            await asyncio.wait_for(transport.prepare_ready.wait(), timeout=1)
            assert transport.prepared[0][0].track_id == tracks[1].id
            assert transport.prepared[0][1] == 7.0

            transport.notify(
                AudioCompleted(
                    transport.attempt_id,
                    12.0,
                    AudioEndReason.NATURAL,
                )
            )
            complete = await _wait_for_command(bus, CompletePlayback)
            assert isinstance(complete, CompletePlayback)
            assert complete.request_id == current_request.id
            assert any(isinstance(command, AdvancePlayback) for command in bus.commands)
            assert any(isinstance(command, FinishPlayback) for command in bus.commands)
            assert transport.presences[-1].title == tracks[0].title
        finally:
            await coordinator.close()
        assert transport.closed

    asyncio.run(scenario())


def test_source_without_first_frame_retries_once_then_returns_request() -> None:
    async def scenario() -> None:
        tracks = (_track(1),)
        state, current_request = _playing_state(tracks)
        transport = FakeTransport(fail_starts=True)
        coordinator, bus = _coordinator(state, tracks, transport)
        try:
            await coordinator.start()
            failed = await _wait_for_command(bus, FailPlayback)
            assert isinstance(failed, FailPlayback)
            assert failed.request_id == current_request.id
            assert transport.play_attempts == 2
            assert not any(
                isinstance(command, BeginPlayback) for command in bus.commands
            )
        finally:
            await coordinator.close()

    asyncio.run(scenario())


def test_voice_connection_retries_are_bounded_and_observable() -> None:
    async def scenario() -> None:
        tracks = (_track(1),)
        state, _request = _playing_state(tracks)
        transport = FakeTransport(fail_connects=10)
        coordinator, bus = _coordinator(
            state,
            tracks,
            transport,
            voice_retry_delays=(0, 0),
        )
        try:
            await coordinator.start()
            failed = await _wait_for_voice_phase(
                bus,
                VoiceConnectionPhase.FAILED,
            )
            assert failed.connection.channel_id == 42
            assert failed.connection.attempt == 3
            assert failed.connection.error == "VoiceError"
            assert coordinator.voice_state == failed.connection
            assert transport.connect_attempts == 3
            await asyncio.sleep(0.01)
            assert transport.connect_attempts == 3
        finally:
            await coordinator.close()

    asyncio.run(scenario())


def test_voice_reconnect_resumes_the_current_track() -> None:
    async def scenario() -> None:
        tracks = (_track(1),)
        state, _request = _playing_state(tracks)
        transport = FakeTransport()
        coordinator, bus = _coordinator(state, tracks, transport)
        try:
            await coordinator.start()
            await asyncio.wait_for(transport.play_ready.wait(), timeout=1)
            assert transport.connection is not None
            assert transport.attempt_id is not None
            attempt_id = transport.attempt_id
            notify = transport.notify
            assert notify is not None
            notify(AudioStarted(attempt_id, 0))
            await _wait_for_command(bus, BeginPlayback)

            connection, _progress = transport.lose_connection()
            coordinator.notify_disconnect(
                VoiceDisconnected(
                    connection,
                    AudioProgress(attempt_id, 21, False, False),
                )
            )

            await asyncio.wait_for(transport.play_ready.wait(), timeout=1)
            assert transport.connect_attempts == 2
            assert transport.play_attempts == 2
            assert coordinator.voice_state.phase is VoiceConnectionPhase.CONNECTED
        finally:
            await coordinator.close()

    asyncio.run(scenario())


def test_shutdown_cancels_a_pending_voice_retry() -> None:
    async def scenario() -> None:
        tracks = (_track(1),)
        state, _request = _playing_state(tracks)
        transport = FakeTransport(fail_connects=10)
        coordinator, bus = _coordinator(
            state,
            tracks,
            transport,
            voice_retry_delays=(60,),
        )
        await coordinator.start()
        await _wait_for_voice_phase(bus, VoiceConnectionPhase.RETRYING)
        await coordinator.close()
        await asyncio.sleep(0)
        assert transport.connect_attempts == 1
        assert transport.closed

    asyncio.run(scenario())

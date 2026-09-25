# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Playback orchestration and transport ports for the shared player session."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Protocol
from uuid import UUID, uuid4

from nahoermaar.catalog.domain import Track
from nahoermaar.catalog.service import CatalogService, ResolvedAudio
from nahoermaar.listening.domain import (
    PlaybackEndReason,
    PlaybackProgress,
    PlaybackRecordId,
)
from nahoermaar.listening.service import (
    AdvancePlayback,
    BeginPlayback,
    DisconnectAudience,
    FinishPlayback,
    ListeningService,
    ObserveAudience,
    VoiceMemberState,
)
from nahoermaar.messaging import MessageBus, MessageContext
from nahoermaar.observability import current_correlation_id

from .domain import (
    OperationId,
    PlaybackCheckpoint,
    PlaybackIntent,
    PlayerAction,
    PlayerState,
    TrackRequest,
)
from .events import (
    CheckpointPlayback,
    CompletePlayback,
    FailPlayback,
    PlayerChanged,
)
from .session import PlayerSessionManager

_LOGGER = logging.getLogger(__name__)
_CHECKPOINT_INTERVAL_SECONDS = 5.0
_MAILBOX_CAPACITY = 64


class AudioError(RuntimeError):
    """Audio preparation or output failed."""


class AudioSourceNotReady(AudioError):
    """A resolved source did not produce a first frame."""


class VoiceError(RuntimeError):
    """A Discord voice connection failed."""


class AudioEndReason(StrEnum):
    NATURAL = "natural"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"
    OUTPUT_FAILED = "output_failed"


@dataclass(frozen=True, slots=True)
class PlayableSource:
    track_id: UUID
    provider: str
    external_id: str
    duration_seconds: float | None
    stream_url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    is_opus: bool = False

    def __post_init__(self) -> None:
        if not self.stream_url or self.stream_url != self.stream_url.strip():
            raise ValueError("Audio stream URL must be non-empty and trimmed.")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds <= 0
        ):
            raise ValueError("Audio duration must be positive and finite.")
        if type(self.headers) is not tuple:
            raise ValueError("Audio headers must be immutable.")


@dataclass(frozen=True, slots=True)
class AudioPosition:
    attempt_id: UUID
    position_seconds: float

    def __post_init__(self) -> None:
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Audio position must be finite and non-negative.")


@dataclass(frozen=True, slots=True)
class AudioProgress(AudioPosition):
    paused: bool = False
    transitioning: bool = False


@dataclass(frozen=True, slots=True)
class AudioStarted(AudioPosition):
    pass


@dataclass(frozen=True, slots=True)
class AudioCompleted(AudioPosition):
    reason: AudioEndReason
    error: Exception | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CrossfadeDue:
    outgoing_attempt_id: UUID
    preparation_id: UUID
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class CrossfadeCompleted:
    attempt_id: UUID
    preparation_id: UUID


type AudioEvent = AudioStarted | AudioCompleted | CrossfadeDue | CrossfadeCompleted


@dataclass(frozen=True, slots=True)
class VoiceChannel:
    id: int
    name: str
    guild_id: int
    guild_name: str
    can_connect: bool
    can_speak: bool


@dataclass(frozen=True, slots=True)
class VoiceConnection:
    connection_id: UUID
    channel_id: int


@dataclass(frozen=True, slots=True)
class VoiceDisconnected:
    connection: VoiceConnection
    progress: AudioProgress | None


@dataclass(frozen=True, slots=True)
class NowPlaying:
    title: str | None = None
    artist: str | None = None
    paused: bool = False


class PlaybackTransport(Protocol):
    @property
    def connection(self) -> VoiceConnection | None: ...

    @property
    def progress(self) -> AudioProgress | None: ...

    def channels(self) -> tuple[VoiceChannel, ...]: ...

    def audience(self) -> tuple[VoiceMemberState, ...]: ...

    def set_disconnect_handler(
        self, handler: Callable[[VoiceDisconnected], None]
    ) -> None: ...

    def set_audience_handler(self, handler: Callable[[], None]) -> None: ...

    async def connect(self, channel_id: int, connection_id: UUID) -> None: ...

    async def disconnect(self, connection_id: UUID) -> None: ...

    async def play(
        self,
        source: PlayableSource,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
        *,
        position_seconds: float = 0,
        paused: bool = False,
    ) -> None: ...

    async def stop(self, attempt_id: UUID) -> None: ...

    def pause(self, attempt_id: UUID) -> None: ...

    def resume(self, attempt_id: UUID) -> None: ...

    def set_volume(self, volume: float) -> None: ...

    async def prepare_next(
        self,
        source: PlayableSource,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        seconds: float,
        notify: Callable[[AudioEvent], None],
    ) -> bool: ...

    async def discard_next(self, preparation_id: UUID) -> None: ...

    def start_transition(
        self,
        *,
        outgoing_attempt_id: UUID,
        preparation_id: UUID,
        attempt_id: UUID,
        notify: Callable[[AudioEvent], None],
    ) -> bool: ...

    async def set_presence(self, presence: NowPlaying) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _LogicalPlayback:
    request: TrackRequest
    attempt_id: UUID
    playback_id: PlaybackRecordId | None = None
    audio_seconds: float = 0.0
    last_position: float = 0.0
    retries: int = 0


@dataclass(slots=True)
class _PreparedPlayback:
    request: TrackRequest
    preparation_id: UUID
    source: PlayableSource
    seconds: float


@dataclass(frozen=True, slots=True)
class _CommittedChange:
    event: PlayerChanged | None
    state: PlayerState
    context: MessageContext


class PlaybackCoordinator:
    """Turn committed player state into targeted Discord and audio effects."""

    __slots__ = (
        "_accepting",
        "_background_tasks",
        "_bus",
        "_catalog",
        "_current",
        "_expected_stops",
        "_listening",
        "_loop",
        "_mailbox",
        "_outgoing",
        "_output_task",
        "_player",
        "_prepare_task",
        "_prepared",
        "_ticker",
        "_transitioning",
        "_transport",
        "_worker",
    )

    def __init__(
        self,
        player: PlayerSessionManager,
        catalog: CatalogService,
        listening: ListeningService,
        bus: MessageBus,
        transport: PlaybackTransport,
    ) -> None:
        self._player = player
        self._catalog = catalog
        self._listening = listening
        self._bus = bus
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mailbox: asyncio.Queue[_CommittedChange | None] = asyncio.Queue(
            _MAILBOX_CAPACITY
        )
        self._worker: asyncio.Task[None] | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._output_task: asyncio.Task[None] | None = None
        self._prepare_task: asyncio.Task[None] | None = None
        self._current: _LogicalPlayback | None = None
        self._outgoing: _LogicalPlayback | None = None
        self._prepared: _PreparedPlayback | None = None
        self._transitioning: _PreparedPlayback | None = None
        self._expected_stops: set[UUID] = set()
        self._accepting = False

    @property
    def operational(self) -> bool:
        """Return whether both long-lived coordinator tasks are running."""
        return (
            self._accepting
            and self._worker is not None
            and not self._worker.done()
            and self._ticker is not None
            and not self._ticker.done()
        )

    async def start(self) -> None:
        if self._accepting:
            return
        self._accepting = True
        self._loop = asyncio.get_running_loop()
        self._transport.set_disconnect_handler(self.notify_disconnect)
        self._transport.set_audience_handler(self.notify_audience)
        self._worker = asyncio.create_task(self._run(), name="playback-coordinator")
        self._ticker = asyncio.create_task(
            self._checkpoints(), name="playback-checkpoints"
        )
        await self._mailbox.put(
            _CommittedChange(None, self._player.state, MessageContext())
        )
        _LOGGER.info("playback.coordinator_started")

    async def player_changed(
        self,
        event: PlayerChanged,
        context: MessageContext,
    ) -> None:
        if not self._accepting:
            return
        try:
            self._mailbox.put_nowait(
                _CommittedChange(event, self._player.state, context)
            )
        except asyncio.QueueFull:
            _LOGGER.error(
                "playback.change_dropped session=%s revision=%d reason=mailbox_full",
                event.session_id,
                event.revision,
            )
            raise RuntimeError("Playback coordinator mailbox is full.") from None

    def notify_audio(self, event: AudioEvent) -> None:
        loop = self._loop
        if self._accepting and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._schedule_audio, event)

    def notify_disconnect(self, event: VoiceDisconnected) -> None:
        loop = self._loop
        if self._accepting and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._schedule_disconnect, event)

    def notify_audience(self) -> None:
        loop = self._loop
        if self._accepting and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._schedule_audience)

    def channels(self) -> tuple[VoiceChannel, ...]:
        return self._transport.channels()

    async def close(self) -> None:
        if not self._accepting:
            return
        self._accepting = False
        await self._sample()
        tasks = tuple(
            task
            for task in (
                self._ticker,
                self._output_task,
                self._prepare_task,
                *self._background_tasks,
            )
            if task is not None
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._mailbox.put(None)
        if self._worker is not None:
            await self._worker
        await self._transport.close()
        _LOGGER.info("playback.coordinator_closed")

    async def _run(self) -> None:
        while True:
            change = await self._mailbox.get()
            try:
                if change is None:
                    return
                await self._apply(change)
            except Exception:
                _LOGGER.exception(
                    "playback.change_failed action=%s revision=%s",
                    change.event.outcome.action.value
                    if change and change.event
                    else "restore",
                    change.state.session.revision if change else None,
                )
            finally:
                self._mailbox.task_done()

    async def _apply(self, change: _CommittedChange) -> None:
        state = change.state
        action = change.event.outcome.action if change.event is not None else None
        self._transport.set_volume(state.session.volume)
        await self._sync_connection(state)

        if action is PlayerAction.PLAYBACK_PAUSED and self._current is not None:
            self._transport.pause(self._current.attempt_id)
        elif action is PlayerAction.PLAYBACK_SEEKED:
            await self._sample()
            await self._restart_current(state.checkpoint)
        elif action in {
            PlayerAction.PLAYBACK_SKIPPED,
            PlayerAction.PLAYBACK_STOPPED,
        }:
            reason = (
                PlaybackEndReason.SKIPPED
                if action is PlayerAction.PLAYBACK_SKIPPED
                else PlaybackEndReason.STOPPED
            )
            await self._retire_current(reason, stop=True)
            await self._sync_output(state.checkpoint)
        elif action is PlayerAction.PLAYBACK_FAILED:
            await self._retire_current(PlaybackEndReason.FAILED, stop=True)
        elif (
            action is PlayerAction.PLAYBACK_COMPLETED
            and self._transitioning is None
            and self._current is not None
        ):
            await self._retire_current(PlaybackEndReason.COMPLETED, stop=False)
            await self._sync_output(state.checkpoint)
        elif action is not PlayerAction.PLAYBACK_CHECKPOINTED:
            await self._sync_output(state.checkpoint)

        await self._publish_presence(state)
        await self._ensure_prepared(state)

    async def _sync_connection(self, state: PlayerState) -> None:
        desired = state.session.channel_id
        current = self._transport.connection
        if desired is None:
            if current is not None:
                await self._sample()
                await self._bus.execute(
                    DisconnectAudience(state.session.id, datetime.now(UTC))
                )
                await self._transport.disconnect(current.connection_id)
            return
        if current is not None and current.channel_id == desired:
            return
        if current is not None:
            await self._transport.disconnect(current.connection_id)
        connection_id = uuid4()
        await self._transport.connect(desired, connection_id)
        await self._observe_audience()

    async def _sync_output(self, checkpoint: PlaybackCheckpoint) -> None:
        request = checkpoint.request
        if request is None:
            if self._current is not None:
                await self._retire_current(PlaybackEndReason.STOPPED, stop=True)
            return
        if self._transport.connection is None:
            return
        if self._current is not None and self._current.request.id == request.id:
            if checkpoint.intent is PlaybackIntent.PAUSED:
                self._transport.pause(self._current.attempt_id)
            else:
                self._transport.resume(self._current.attempt_id)
            return
        await self._start_request(
            request, checkpoint.position_seconds, checkpoint.intent
        )

    async def _restart_current(self, checkpoint: PlaybackCheckpoint) -> None:
        current = self._current
        if current is None or checkpoint.request is None:
            await self._sync_output(checkpoint)
            return
        await self._stop_attempt(current.attempt_id)
        current.attempt_id = uuid4()
        current.last_position = checkpoint.position_seconds
        await self._start_source(
            current, checkpoint.position_seconds, checkpoint.intent
        )

    async def _start_request(
        self,
        request: TrackRequest,
        position_seconds: float,
        intent: PlaybackIntent,
    ) -> None:
        if self._output_task is not None:
            self._output_task.cancel()
            await asyncio.gather(self._output_task, return_exceptions=True)
        existing = await self._listening.active_playback(request.session_id, request.id)
        logical = _LogicalPlayback(
            request,
            uuid4(),
            existing.id if existing is not None else None,
            existing.audio_seconds if existing is not None else 0.0,
            position_seconds,
        )
        self._current = logical
        self._output_task = asyncio.create_task(
            self._start_source(logical, position_seconds, intent),
            name=f"playback-output-{logical.attempt_id}",
        )

    async def _start_source(
        self,
        logical: _LogicalPlayback,
        position_seconds: float,
        intent: PlaybackIntent,
    ) -> None:
        try:
            source = await self._resolve(logical.request)
            if self._current is not logical:
                return
            await self._transport.play(
                source,
                logical.attempt_id,
                self.notify_audio,
                position_seconds=position_seconds,
                paused=intent is PlaybackIntent.PAUSED,
            )
        except asyncio.CancelledError:
            await self._stop_attempt(logical.attempt_id)
            raise
        except AudioSourceNotReady:
            await self._retry_or_fail(logical, retryable=True)
        except Exception:
            _LOGGER.exception(
                "playback.output_start_failed request_id=%s attempt_id=%s",
                logical.request.id,
                logical.attempt_id,
            )
            await self._retry_or_fail(logical, retryable=False)

    async def _retry_or_fail(
        self,
        logical: _LogicalPlayback,
        *,
        retryable: bool,
    ) -> None:
        if self._current is not logical:
            return
        if retryable and logical.retries < 1:
            logical.retries += 1
            logical.attempt_id = uuid4()
            checkpoint = self._player.state.checkpoint
            await self._start_source(
                logical,
                checkpoint.position_seconds,
                checkpoint.intent,
            )
            return
        await self._finish(logical, PlaybackEndReason.FAILED)
        self._current = None
        await self._bus.execute(
            FailPlayback(
                logical.request.session_id,
                self._operation_id(),
                logical.request.id,
            )
        )

    async def _resolve(self, request: TrackRequest) -> PlayableSource:
        resolved: ResolvedAudio = await self._catalog.resolve_audio(
            request.track_id,
            request.source_id,
        )
        return PlayableSource(
            resolved.track.id,
            resolved.source.provider.value,
            resolved.source.external_id,
            resolved.track.duration_seconds
            or resolved.source.observed_duration_seconds,
            resolved.stream_url,
            resolved.headers,
            resolved.is_opus,
        )

    async def _retire_current(
        self,
        reason: PlaybackEndReason,
        *,
        stop: bool,
    ) -> None:
        current = self._current
        if current is None:
            return
        await self._sample()
        if stop:
            await self._stop_attempt(current.attempt_id)
        await self._finish(current, reason)
        if self._current is current:
            self._current = None

    async def _stop_attempt(self, attempt_id: UUID) -> None:
        self._expected_stops.add(attempt_id)
        try:
            await self._transport.stop(attempt_id)
        finally:
            self._expected_stops.discard(attempt_id)

    async def _finish(
        self,
        logical: _LogicalPlayback,
        reason: PlaybackEndReason,
    ) -> None:
        if logical.playback_id is None:
            return
        await self._bus.execute(
            FinishPlayback(
                logical.playback_id,
                datetime.now(UTC),
                reason,
            )
        )

    async def _sample(self, position: float | None = None) -> None:
        current = self._current
        if current is None or current.playback_id is None:
            return
        progress = self._transport.progress
        if position is None:
            if progress is None or progress.attempt_id != current.attempt_id:
                return
            position = progress.position_seconds
        delta = max(0.0, position - current.last_position)
        current.audio_seconds += delta
        current.last_position = position
        samples = [PlaybackProgress(current.playback_id, current.audio_seconds)]
        if self._outgoing is not None and self._outgoing.playback_id is not None:
            samples.insert(
                0,
                PlaybackProgress(
                    self._outgoing.playback_id,
                    self._outgoing.audio_seconds,
                ),
            )
        await self._bus.execute(
            AdvancePlayback(
                current.request.session_id,
                tuple(samples),
                current.playback_id,
                datetime.now(UTC),
            )
        )
        checkpoint = self._player.state.checkpoint
        if (
            checkpoint.request is not None
            and checkpoint.request.id == current.request.id
            and abs(checkpoint.position_seconds - position) >= 1
        ):
            await self._bus.execute(
                CheckpointPlayback(
                    current.request.session_id,
                    self._operation_id(),
                    current.request.id,
                    position,
                )
            )

    async def _ensure_prepared(self, state: PlayerState) -> None:
        current = self._current
        if (
            current is None
            or current.playback_id is None
            or self._transitioning is not None
            or self._transport.connection is None
        ):
            return
        head = state.queue.entries[0].request if state.queue.entries else None
        if head is None:
            await self._discard_prepared()
            return
        if self._prepared is not None and self._prepared.request.id == head.id:
            return
        await self._discard_prepared()
        if self._prepare_task is not None:
            self._prepare_task.cancel()
            await asyncio.gather(self._prepare_task, return_exceptions=True)
        self._prepare_task = asyncio.create_task(
            self._prepare(head, current, state.session.crossfade_seconds),
            name=f"playback-prepare-{head.id}",
        )

    async def _prepare(
        self,
        request: TrackRequest,
        outgoing: _LogicalPlayback,
        seconds: int,
    ) -> None:
        preparation_id = uuid4()
        try:
            source = await self._resolve(request)
            accepted = await self._transport.prepare_next(
                source,
                outgoing_attempt_id=outgoing.attempt_id,
                preparation_id=preparation_id,
                seconds=float(seconds),
                notify=self.notify_audio,
            )
            if accepted and self._current is outgoing:
                self._prepared = _PreparedPlayback(
                    request,
                    preparation_id,
                    source,
                    float(seconds),
                )
        except asyncio.CancelledError:
            await self._transport.discard_next(preparation_id)
            raise
        except Exception:
            _LOGGER.exception(
                "playback.prepare_failed request_id=%s preparation_id=%s",
                request.id,
                preparation_id,
            )

    async def _discard_prepared(self) -> None:
        prepared, self._prepared = self._prepared, None
        if prepared is not None:
            await self._transport.discard_next(prepared.preparation_id)

    def _schedule_audio(self, event: AudioEvent) -> None:
        self._track_task(
            asyncio.create_task(self._handle_audio(event), name="playback-audio-event")
        )

    async def _handle_audio(self, event: AudioEvent) -> None:
        if isinstance(event, AudioStarted):
            current = self._current
            if current is None or current.attempt_id != event.attempt_id:
                return
            current.last_position = event.position_seconds
            if current.playback_id is None:
                current.playback_id = PlaybackRecordId(uuid4())
                await self._bus.execute(
                    BeginPlayback(
                        current.playback_id,
                        current.request.session_id,
                        current.request.id,
                        datetime.now(UTC),
                    )
                )
            await self._ensure_prepared(self._player.state)
            return

        if isinstance(event, CrossfadeDue):
            await self._start_crossfade(event)
            return
        if isinstance(event, CrossfadeCompleted):
            await self._finish_crossfade(event)
            return
        if event.attempt_id in self._expected_stops:
            return
        current = self._current
        if current is None or current.attempt_id != event.attempt_id:
            return
        await self._sample(event.position_seconds)
        if event.reason is AudioEndReason.NATURAL:
            await self._finish(current, PlaybackEndReason.COMPLETED)
            self._current = None
            await self._bus.execute(
                CompletePlayback(
                    current.request.session_id,
                    self._operation_id(),
                    current.request.id,
                )
            )
        elif event.reason is AudioEndReason.INTERRUPTED:
            await self._retry_or_fail(current, retryable=True)
        elif event.reason is not AudioEndReason.STOPPED:
            await self._retry_or_fail(current, retryable=False)

    async def _start_crossfade(self, event: CrossfadeDue) -> None:
        current, prepared = self._current, self._prepared
        state = self._player.state
        if (
            current is None
            or prepared is None
            or current.attempt_id != event.outgoing_attempt_id
            or prepared.preparation_id != event.preparation_id
            or not state.queue.entries
            or state.queue.entries[0].request.id != prepared.request.id
            or state.checkpoint.intent is PlaybackIntent.PAUSED
        ):
            if prepared is not None:
                await self._discard_prepared()
            return
        self._transitioning = prepared
        self._prepared = None
        await self._sample()
        await self._bus.execute(
            CompletePlayback(
                current.request.session_id,
                self._operation_id(),
                current.request.id,
            )
        )
        checkpoint = self._player.state.checkpoint
        if checkpoint.request is None or checkpoint.request.id != prepared.request.id:
            self._transitioning = None
            await self._transport.discard_next(prepared.preparation_id)
            return
        incoming = _LogicalPlayback(
            prepared.request,
            uuid4(),
            None,
            0.0,
            0.0,
        )
        self._outgoing = current
        self._current = incoming
        accepted = self._transport.start_transition(
            outgoing_attempt_id=event.outgoing_attempt_id,
            preparation_id=prepared.preparation_id,
            attempt_id=incoming.attempt_id,
            notify=self.notify_audio,
        )
        if not accepted:
            self._current = None
            self._outgoing = None
            self._transitioning = None
            await self._finish(current, PlaybackEndReason.COMPLETED)
            await self._start_request(
                prepared.request,
                0.0,
                checkpoint.intent,
            )

    async def _finish_crossfade(self, event: CrossfadeCompleted) -> None:
        current, outgoing, transition = (
            self._current,
            self._outgoing,
            self._transitioning,
        )
        if (
            current is None
            or outgoing is None
            or transition is None
            or current.attempt_id != event.attempt_id
            or transition.preparation_id != event.preparation_id
        ):
            return
        outgoing.audio_seconds += transition.seconds
        await self._sample()
        await self._finish(outgoing, PlaybackEndReason.COMPLETED)
        self._outgoing = None
        self._transitioning = None
        await self._ensure_prepared(self._player.state)

    def _schedule_disconnect(self, event: VoiceDisconnected) -> None:
        self._track_task(
            asyncio.create_task(
                self._handle_disconnect(event),
                name="playback-voice-disconnected",
            )
        )

    async def _handle_disconnect(self, event: VoiceDisconnected) -> None:
        if event.progress is not None:
            await self._sample(event.progress.position_seconds)
        await self._bus.execute(
            DisconnectAudience(
                self._player.state.session.id,
                datetime.now(UTC),
            )
        )
        await self._mailbox.put(
            _CommittedChange(None, self._player.state, MessageContext())
        )

    def _schedule_audience(self) -> None:
        self._track_task(
            asyncio.create_task(self._observe_audience(), name="playback-audience")
        )

    def _track_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _observe_audience(self) -> None:
        if self._transport.connection is None:
            return
        await self._sample()
        await self._bus.execute(
            ObserveAudience(
                self._player.state.session.id,
                self._transport.audience(),
                datetime.now(UTC),
            )
        )

    async def _publish_presence(self, state: PlayerState) -> None:
        checkpoint = state.checkpoint
        if checkpoint.request is None:
            await self._transport.set_presence(NowPlaying())
            return
        tracks = await self._catalog.tracks({checkpoint.request.track_id})
        track: Track | None = tracks.get(checkpoint.request.track_id)
        if track is None:
            await self._transport.set_presence(NowPlaying())
            return
        artist = ", ".join(credit.artist.name for credit in track.artists) or None
        await self._transport.set_presence(
            NowPlaying(
                track.title,
                artist,
                checkpoint.intent is PlaybackIntent.PAUSED,
            )
        )

    async def _checkpoints(self) -> None:
        while True:
            await asyncio.sleep(_CHECKPOINT_INTERVAL_SECONDS)
            try:
                await self._sample()
                await self._ensure_prepared(self._player.state)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("playback.checkpoint_failed")

    @staticmethod
    def _operation_id() -> OperationId:
        return OperationId(current_correlation_id())


def pause(checkpoint: PlaybackCheckpoint) -> PlaybackCheckpoint:
    if checkpoint.intent is not PlaybackIntent.PLAYING:
        raise ValueError("Playback is not playing.")
    return replace(checkpoint, intent=PlaybackIntent.PAUSED)


def resume(checkpoint: PlaybackCheckpoint) -> PlaybackCheckpoint:
    if checkpoint.intent is not PlaybackIntent.PAUSED:
        raise ValueError("Playback is not paused.")
    return replace(checkpoint, intent=PlaybackIntent.PLAYING)

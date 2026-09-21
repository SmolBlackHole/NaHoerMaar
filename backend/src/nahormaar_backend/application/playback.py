# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Playback attempts and audio effects inside the Session mutation boundary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.commands import Revisions
from ..domain.fsm import (
    LifecycleEvent,
    PlaybackContext,
    PlaybackDecision,
    PlaybackEffect,
    decide_playback,
)
from ..domain.models import PlaybackState, PlayerSnapshot, QueueEntry
from ..domain.radio import RadioStatus
from .audio import (
    AudioCompleted,
    AudioEndReason,
    AudioStarted,
    ResolvedTrack,
    SourceResolver,
    TrackError,
    VoiceDisconnected,
    VoiceError,
    VoiceOutput,
)
from .crossfade import CrossfadePreparation, Preparation
from .status import PlaybackIssue, PlaybackStatus
from .storage import StorageError

if TYPE_CHECKING:
    from .session import SessionState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceReady:
    attempt_id: UUID
    entry_id: UUID
    track: ResolvedTrack


@dataclass(frozen=True, slots=True)
class SourceFailed:
    attempt_id: UUID
    error: Exception


@dataclass(frozen=True, slots=True)
class CrossfadeReady:
    key: Preparation
    track: ResolvedTrack


@dataclass(frozen=True, slots=True)
class PreparationFailed:
    key: Preparation
    error: VoiceError


@dataclass(frozen=True, slots=True)
class FadeFinished:
    attempt_id: UUID | None


type PlaybackMessage = (
    SourceReady
    | SourceFailed
    | AudioCompleted
    | AudioStarted
    | VoiceDisconnected
    | CrossfadeReady
    | PreparationFailed
    | FadeFinished
)


class PlaybackController:
    """Own audio attempts, never queues, discovery services or command receipts.

    Mutating operations run in the Session's existing serialized path. Resolver
    and audio callbacks return as messages through the injected Session inbox.
    """

    def __init__(
        self,
        state: SessionState,
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        post: Callable[[PlaybackMessage], None],
        stop_radio: Callable[[], None],
        checkpoint: PlaybackCheckpoint | None = None,
    ) -> None:
        self._state = state
        self._resolver = resolver
        self._voice = voice
        self._post = post
        self._stop_radio = stop_radio
        self._crossfade = CrossfadePreparation(resolver, voice)
        self._deferred_fade: tuple[Preparation, ResolvedTrack] | None = None
        self._loop = asyncio.get_running_loop()
        self._context = decide_playback(
            state.snapshot,
            PlaybackContext(),
            LifecycleEvent.RECOVER,
            checkpoint=checkpoint,
        ).context
        self._volume = checkpoint.volume if checkpoint else 1.0
        self._last_issue: PlaybackIssue | None = None
        self._position_seconds = 0.0
        self._resolved_track: ResolvedTrack | None = None
        self._position_updated_at: datetime | None = None
        self._position_clock: float | None = None
        self._load_task: asyncio.Task[None] | None = None
        voice.set_disconnect_handler(self._post_from_thread)

    @property
    def attempt_id(self) -> UUID | None:
        return self._context.attempt_id

    def status(self, revisions: Revisions, radio: RadioStatus) -> PlaybackStatus:
        return PlaybackStatus(
            self._state.snapshot,
            self.attempt_id,
            self._voice.channel_id,
            self._volume,
            self._last_issue,
            revisions.revision,
            revisions.queue_revision,
            self._position_seconds,
            self._position_updated_at,
            radio,
        )

    async def restore(self) -> None:
        """Called once the Discord gateway is ready, before serving controls."""
        channel_id = self._context.channel_id
        if not self._context.rejoin or channel_id is None or self._voice.connected:
            return
        _LOGGER.info(
            "playback.restoring channel=%s entry=%s position=%.3f paused=%s",
            channel_id,
            self._context.entry_id,
            self._context.position_seconds,
            self._context.paused,
        )
        self._voice.set_volume(self._volume)
        await self.connect(channel_id, restoring=True)

    async def handle(self, message: PlaybackMessage) -> None:
        """Apply one technical outcome inside the Session's mutation boundary."""
        match message:
            case SourceReady():
                await self._source_ready(message)
            case SourceFailed(attempt_id, error):
                if self.attempt_id == attempt_id:
                    if isinstance(error, TrackError):
                        await self._track_failed(error)
                    else:
                        await self.fault("Source resolver failed; restart the backend.")
            case AudioCompleted():
                await self._audio_completed(message)
            case AudioStarted(attempt_id, position):
                await self._apply(
                    self._decide(
                        LifecycleEvent.STARTED,
                        attempt_id=attempt_id,
                        position_seconds=position,
                    )
                )
            case VoiceDisconnected():
                if (
                    not self._voice.connected
                    and message.channel_id == self._context.channel_id
                    and (
                        message.attempt_id is None
                        or message.attempt_id == self.attempt_id
                    )
                ):
                    self._last_issue = PlaybackIssue(
                        None, "Discord voice connection lost."
                    )
                    await self._suspend(
                        explicit=False, position=message.position_seconds
                    )
            case CrossfadeReady(key, track):
                await self._start_crossfade(key, track)
            case PreparationFailed(key, error):
                if key == self._crossfade.key and key == self._eligible_preparation():
                    _LOGGER.warning(
                        "crossfade.preparation_failed entry=%s error=%s",
                        key.entry_id,
                        type(error).__name__,
                    )
            case FadeFinished(attempt_id):
                if attempt_id == self.attempt_id:
                    _LOGGER.info("crossfade.finished attempt=%s", attempt_id)
                    await self.prepare_crossfade()

    async def save_checkpoint(self) -> None:
        checkpoint = self._checkpoint(
            self._state.snapshot,
            replace(
                self._context,
                position_seconds=self._position(),
            ),
        )
        await self._state.worker.call(lambda player: player.save_checkpoint(checkpoint))

    def _checkpoint(
        self, snapshot: PlayerSnapshot, context: PlaybackContext
    ) -> PlaybackCheckpoint | None:
        if not context.rejoin or context.channel_id is None or self._state.faulted:
            return None
        return PlaybackCheckpoint(
            context.channel_id,
            snapshot.current.id if snapshot.current else None,
            context.position_seconds if snapshot.current else 0,
            context.paused if snapshot.current else False,
            self._volume,
            history_recorded=context.history_recorded if snapshot.current else False,
        )

    def _position(self) -> float:
        if self.attempt_id is not None and self._voice.position_seconds is not None:
            return self._voice.position_seconds
        return self._context.position_seconds

    def _decide(
        self,
        event: LifecycleEvent,
        *,
        attempt_id: UUID | None = None,
        position_seconds: float | None = None,
        channel_id: int | None = None,
    ) -> PlaybackDecision:
        return decide_playback(
            self._state.snapshot,
            self._context,
            event,
            attempt_id=attempt_id,
            position_seconds=position_seconds,
            channel_id=channel_id,
        )

    async def _apply(
        self,
        decision: PlaybackDecision,
        *,
        track: ResolvedTrack | None = None,
        error: Exception | None = None,
    ) -> None:
        if decision == PlaybackDecision(self._state.snapshot, self._context):
            return
        previous = self._state.snapshot.current
        context = decision.context
        if PlaybackEffect.LOAD in decision.effects:
            context = replace(context, attempt_id=uuid4())
        checkpoint = self._checkpoint(decision.snapshot, context)
        await self._state.change(
            lambda player: player.commit_lifecycle(
                decision.snapshot,
                checkpoint,
                record_history=PlaybackEffect.RECORD_HISTORY in decision.effects,
            )
        )
        self._context = context
        for effect in decision.effects:
            match effect:
                case PlaybackEffect.STOP_OUTPUT:
                    await self._release_output()
                case PlaybackEffect.LOAD:
                    if track is None:
                        self._begin()
                    else:
                        await self._start_output(track)
                case PlaybackEffect.RESUME_OUTPUT:
                    self._voice.resume()
                case PlaybackEffect.PAUSE_OUTPUT:
                    self._voice.pause()
                case PlaybackEffect.REPORT_FAILURE:
                    self._last_issue = PlaybackIssue(
                        previous.id if previous else None,
                        str(error)
                        if error
                        else "The source ended before playback started.",
                        entry=previous,
                        reason="stream_interrupted"
                        if isinstance(error, TrackError) and error.retryable
                        else "source_unavailable",
                    )
                case PlaybackEffect.RECORD_HISTORY:
                    _LOGGER.info(
                        "playback.started attempt=%s entry=%s position=%.3f",
                        context.attempt_id,
                        context.entry_id,
                        context.position_seconds,
                    )

    async def prepare_crossfade(self) -> None:
        key = self._eligible_preparation()
        await self._crossfade.sync(key, self._crossfade_due, self._preparation_failed)
        if (
            self._deferred_fade is not None
            and self._state.snapshot.state is PlaybackState.PLAYING
        ):
            deferred, self._deferred_fade = self._deferred_fade, None
            self._crossfade_due(*deferred)

    def _eligible_preparation(self) -> Preparation | None:
        snapshot = self._state.snapshot
        track = self._resolved_track
        if (
            not self._state.closing
            and not self._state.faulted
            and snapshot.state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
            and snapshot.crossfade_seconds
            and snapshot.upcoming
            and self.attempt_id is not None
            and track is not None
            and track.duration_seconds is not None
            and track.duration_seconds > 0
            and not self._voice.transitioning
        ):
            entry = snapshot.upcoming[0]
            return Preparation(
                self.attempt_id,
                entry.id,
                snapshot.crossfade_seconds,
                entry.source_url,
                track.duration_seconds,
            )
        return None

    def _preparation_failed(self, key: Preparation, error: VoiceError) -> None:
        self._post_callback(PreparationFailed(key, error))

    def _crossfade_due(self, key: Preparation, track: ResolvedTrack) -> None:
        self._post_from_thread(CrossfadeReady(key, track))

    async def _start_crossfade(self, key: Preparation, track: ResolvedTrack) -> None:
        if key != self._crossfade.key or key != self._eligible_preparation():
            return
        if self._state.snapshot.state is PlaybackState.PAUSED:
            self._deferred_fade = (key, track)
            return
        if not self._voice.connected:
            await self._suspend(explicit=False)
            return
        _LOGGER.info(
            "crossfade.start outgoing_attempt=%s incoming_entry=%s seconds=%s "
            "position=%s",
            key.attempt_id,
            key.entry_id,
            key.seconds,
            self._voice.position_seconds,
        )
        decision = self._decide(LifecycleEvent.CROSSFADE)
        attempt_id = uuid4()
        await self._apply(
            replace(decision, context=replace(decision.context, attempt_id=attempt_id))
        )
        await self._state.change(
            lambda player: player.enrich(key.entry_id, track.metadata)
        )
        self._resolved_track = track
        try:
            activated = self._voice.start_transition(
                track,
                attempt_id,
                self._post_from_thread,
                lambda: self._post_from_thread(FadeFinished(attempt_id)),
            )
        except (TrackError, VoiceError):
            activated = False
        if not activated:
            _LOGGER.warning(
                "crossfade.fallback attempt=%s entry=%s reason=activation_unavailable",
                attempt_id,
                key.entry_id,
            )
            # EOF may win while reservation commits. Only a confirmed frame counts.
            # Fallback is a new output attempt. A stopped partial activation must
            # not complete the fallback through its late callback.
            await self._apply(
                PlaybackDecision(
                    self._state.snapshot,
                    replace(self._context, attempt_id=uuid4()),
                )
            )
            await self._voice.stop()
            await self._start_output(track)

    def _post_from_thread(self, message: PlaybackMessage) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._post_callback, message)

    def _post_callback(self, message: PlaybackMessage) -> None:
        if self._state.closing:
            return
        if self._state.faulted and isinstance(
            message, (CrossfadeReady, PreparationFailed, FadeFinished)
        ):
            return
        self._post(message)

    def update_position(self, previous: PlaybackStatus) -> None:
        now = self._loop.time()
        if previous.attempt_id != self.attempt_id:
            self._position_seconds = self._context.position_seconds
        elif previous.player.state is self._state.snapshot.state:
            return
        elif previous.player.state is PlaybackState.LOADING:
            self._position_seconds = self._context.position_seconds
        elif (
            previous.player.state is PlaybackState.PLAYING
            and self._position_clock is not None
        ):
            self._position_seconds = self._position()
        self._position_clock = now
        self._position_updated_at = datetime.now(UTC)

    async def connect(self, channel_id: int, *, restoring: bool = False) -> None:
        _LOGGER.info(
            "voice.connect requested=%s previous=%s", channel_id, self._voice.channel_id
        )
        if not (self._voice.connected and self._voice.channel_id == channel_id):
            if self.attempt_id is not None:
                await self._suspend(explicit=False)
            await self._voice.disconnect()
            await self._apply(self._decide(LifecycleEvent.CONNECTING))
            await self._voice.connect(channel_id)
        await self._apply(
            self._decide(
                LifecycleEvent.RESTORED if restoring else LifecycleEvent.JOINED,
                channel_id=channel_id,
            )
        )

    async def leave(self) -> None:
        _LOGGER.info("voice.leave channel=%s", self._voice.channel_id)
        self._stop_radio()
        await self._suspend(explicit=True)
        await self._voice.disconnect()

    async def _suspend(self, *, explicit: bool, position: float | None = None) -> None:
        await self._apply(
            self._decide(
                LifecycleEvent.LEAVE if explicit else LifecycleEvent.DISCONNECTED,
                position_seconds=self._position() if position is None else position,
            )
        )

    async def play(self) -> None:
        if not self._voice.connected:
            raise ValueError("Join a voice channel before starting playback.")
        self._last_issue = None
        await self._apply(self._decide(LifecycleEvent.PLAY))

    async def pause(self) -> None:
        await self._apply(
            self._decide(LifecycleEvent.PAUSE, position_seconds=self._position())
        )

    async def skip(self) -> None:
        _LOGGER.info(
            "playback.skip attempt=%s position=%s",
            self.attempt_id,
            self._voice.position_seconds,
        )
        if not self._voice.connected:
            await self._suspend(explicit=False)
            return
        await self._apply(self._decide(LifecycleEvent.SKIP))

    async def stop(self) -> None:
        _LOGGER.info(
            "playback.stop attempt=%s position=%s",
            self.attempt_id,
            self._voice.position_seconds,
        )
        self._stop_radio()
        await self._apply(self._decide(LifecycleEvent.STOP))

    def set_volume(self, volume: float) -> None:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        if volume != self._volume:
            self._voice.set_volume(volume)
            self._volume = volume

    async def seek(self, position_seconds: float) -> None:
        entry = self._state.snapshot.current
        track = self._resolved_track
        if (
            not isfinite(position_seconds)
            or entry is None
            or entry.duration_seconds is None
            or not 0 <= position_seconds < entry.duration_seconds
            or track is None
        ):
            raise ValueError("Seek to a position within the current track.")
        if not self._voice.connected:
            raise VoiceError("Discord voice is not connected.")
        _LOGGER.info(
            "playback.seek attempt=%s from=%s to=%.3f",
            self.attempt_id,
            self._voice.position_seconds,
            position_seconds,
        )
        await self._apply(
            self._decide(LifecycleEvent.SEEK, position_seconds=position_seconds),
            track=track,
        )

    def _begin(self) -> None:
        entry = self._state.snapshot.current
        attempt_id = self.attempt_id
        if entry is None or attempt_id is None:
            return
        _LOGGER.info(
            "playback.loading attempt=%s entry=%s video=%s position=%.3f retry=%s paused=%s",
            attempt_id,
            entry.id,
            entry.video_id,
            self._context.position_seconds,
            self._context.retries,
            self._context.paused,
        )
        self._load_task = asyncio.create_task(self._load(attempt_id, entry))

    async def _load(self, attempt_id: UUID, entry: QueueEntry) -> None:
        try:
            track = await self._resolver.resolve(entry.source_url)
        except asyncio.CancelledError:
            raise
        except TrackError as exc:
            self._post(SourceFailed(attempt_id, exc))
            return
        except Exception as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise VoiceError("Source extraction cleanup failed.") from exc

            self._post(SourceFailed(attempt_id, exc))
            return

        self._post(SourceReady(attempt_id, entry.id, track))

    async def _source_ready(self, message: SourceReady) -> None:
        attempt_id, entry_id, track = (
            message.attempt_id,
            message.entry_id,
            message.track,
        )
        if self.attempt_id != attempt_id:
            return
        if not self._voice.connected:
            await self._suspend(explicit=False)
            return
        await self._state.change(lambda player: player.enrich(entry_id, track.metadata))
        await self._start_output(track)

    async def _start_output(self, track: ResolvedTrack) -> None:
        attempt_id = self.attempt_id
        if attempt_id is None:
            return
        position = self._context.position_seconds
        if track.duration_seconds is not None:
            position = min(position, max(0, track.duration_seconds - 0.02))
        self._context = replace(self._context, position_seconds=position)
        self._resolved_track = track
        try:
            self._voice.play(
                track,
                attempt_id,
                self._post_from_thread,
                position_seconds=position,
                paused=self._context.paused,
            )
        except TrackError as exc:
            await self._track_failed(exc)
        except VoiceError as exc:
            await self._apply(
                self._decide(
                    LifecycleEvent.FAILED,
                    attempt_id=attempt_id,
                    position_seconds=position,
                ),
                error=exc,
            )

    async def _audio_completed(self, message: AudioCompleted) -> None:
        attempt_id, error = message.attempt_id, message.error
        if self.attempt_id != attempt_id:
            return
        _LOGGER.info(
            "playback.completed attempt=%s position=%s duration=%s error=%s reason=%s",
            attempt_id,
            message.position_seconds,
            self._resolved_track.duration_seconds if self._resolved_track else None,
            type(error).__name__ if error is not None else "none",
            message.reason,
        )
        if not self._voice.connected:
            self._last_issue = PlaybackIssue(None, "Discord voice connection lost.")
            await self._suspend(explicit=False, position=message.position_seconds)
            return
        event = {
            AudioEndReason.NATURAL: LifecycleEvent.FINISHED,
            AudioEndReason.INTERRUPTED: LifecycleEvent.INTERRUPTED,
            AudioEndReason.STOPPED: LifecycleEvent.STOPPED,
            AudioEndReason.OUTPUT_FAILED: LifecycleEvent.FAILED,
        }[message.reason]
        decision = self._decide(
            event, attempt_id=attempt_id, position_seconds=message.position_seconds
        )
        if event in (LifecycleEvent.INTERRUPTED, LifecycleEvent.FAILED):
            _LOGGER.warning(
                "playback.failed attempt=%s position=%.3f retryable=%s action=%s",
                attempt_id,
                message.position_seconds,
                event is LifecycleEvent.INTERRUPTED,
                "skip"
                if PlaybackEffect.REPORT_FAILURE in decision.effects
                else "retry",
            )
        await self._apply(decision, error=error)

    async def _track_failed(self, error: TrackError) -> None:
        if not self._voice.connected:
            await self._suspend(explicit=False)
            return
        entry = self._state.snapshot.current
        if entry is None:
            return
        decision = self._decide(
            LifecycleEvent.INTERRUPTED if error.retryable else LifecycleEvent.FAILED,
            attempt_id=self.attempt_id,
            position_seconds=self._position(),
        )
        _LOGGER.warning(
            "playback.failed attempt=%s entry=%s position=%s retryable=%s action=%s",
            self.attempt_id,
            entry.id,
            self._position(),
            error.retryable,
            "skip" if PlaybackEffect.REPORT_FAILURE in decision.effects else "retry",
        )
        await self._apply(decision, error=error)

    async def halt(self) -> None:
        self._context = replace(self._context, attempt_id=None)
        await self._release_output()

    async def _release_output(self) -> None:
        self._resolved_track = None
        self._deferred_fade = None
        task, self._load_task = self._load_task, None
        if task is not None and not task.done():
            task.cancel()
        # Stop the audible source immediately while speculative work is reaped.
        results = await asyncio.gather(
            self._voice.stop(),
            self._crossfade.clear(),
            *((task,) if task is not None else ()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def fault(self, message: str, *, persist_failure: bool = True) -> None:
        _LOGGER.error(
            "playback.fault attempt=%s persist_failure=%s",
            self.attempt_id,
            persist_failure,
        )
        self._stop_radio()
        self._state.faulted = True
        entry = self._state.snapshot.current
        if persist_failure and self._state.snapshot.state in (
            PlaybackState.LOADING,
            PlaybackState.PLAYING,
            PlaybackState.PAUSED,
        ):
            try:
                await self._apply(self._decide(LifecycleEvent.FAULT))
            except StorageError:
                message = f"{message} The error state could not be saved."
        self._last_issue = PlaybackIssue(entry.id if entry else None, message, True)
        try:
            await self.halt()
        except Exception:
            self._last_issue = PlaybackIssue(
                entry.id if entry else None,
                f"{message} Audio cleanup also failed.",
                True,
            )

    async def voice_failed(self, message: str) -> None:
        self._last_issue = PlaybackIssue(None, message)
        try:
            await self._suspend(explicit=False)
        except Exception as exc:
            await self.fault(
                f"{message} Could not restore the stopped player.",
                persist_failure=not isinstance(exc, StorageError),
            )

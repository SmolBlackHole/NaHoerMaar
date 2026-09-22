# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Execute committed FSM effects. The Session remains the only state owner."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from .audio import AudioPlayer, VoiceConnection, VoiceTransport
from .catalog import Catalog
from .domain.playback import (
    Activate,
    AttemptFailed,
    ChangePause,
    Connect,
    Control,
    Disconnect,
    DiscardNext,
    Effect,
    Joined,
    JoinFailed,
    PlaybackMessage,
    Prepared,
    PrepareNext,
    SetVolume,
    SourceResolved,
    StartAttempt,
    StopOutput,
    TransitionFailed,
)
from .domain.sessions import PlaybackIntent, PlaybackPhase, SessionSnapshot

_LOGGER = logging.getLogger(__name__)


class PlaybackController:
    """Borrow adapters; own only jobs and their cleanup, never a player model.

    Replacing an output/voice/preparation job cancels and settles its predecessor
    before starting. Adapter operations additionally target explicit identities.
    """

    def __init__(
        self,
        catalog: Catalog,
        audio: AudioPlayer,
        voice: VoiceTransport,
        *,
        report: Callable[[PlaybackMessage], Awaitable[SessionSnapshot]],
        snapshot: Callable[[], SessionSnapshot],
    ) -> None:
        self.audio, self.voice = audio, voice
        self._catalog, self._report, self._snapshot = catalog, report, snapshot
        self._loop = asyncio.get_running_loop()
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._cleanup_lanes: set[str] = set()
        self._deliveries: set[asyncio.Task[None]] = set()
        self._accepting = True
        voice.set_disconnect_handler(self.notify)
        self._ticker = asyncio.create_task(
            self._checkpoints(), name="engine-checkpoints"
        )

    def notify(self, event: PlaybackMessage) -> None:
        # Called by Discord's audio thread as well as by asyncio tasks.
        if self._accepting and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: PlaybackMessage) -> None:
        if not self._accepting:
            return

        async def run() -> None:
            try:
                await self._report(event)
            except Exception:
                _LOGGER.exception(
                    "engine.playback.fact_commit_failed",
                    extra={"fact": type(event).__name__},
                )

        task = asyncio.create_task(run(), name="engine-playback-fact")
        self._deliveries.add(task)
        task.add_done_callback(self._deliveries.discard)

    def _replace(
        self,
        lane: str,
        operation: Callable[[], Awaitable[None]],
        *,
        cleanup: bool = False,
    ) -> None:
        previous = self._jobs.get(lane)
        if previous and lane not in self._cleanup_lanes:
            if not previous.done():
                _LOGGER.info("engine.playback.effect_replaced lane=%s", lane)
            previous.cancel()
        if cleanup:
            self._cleanup_lanes.add(lane)
        else:
            self._cleanup_lanes.discard(lane)

        async def run() -> None:
            if previous:
                # Cancelling a chain must still settle its older resource cleanup.
                try:
                    await asyncio.shield(
                        asyncio.gather(previous, return_exceptions=True)
                    )
                except asyncio.CancelledError:
                    await asyncio.gather(previous, return_exceptions=True)
                    raise
            await operation()

        task = asyncio.create_task(run(), name=f"engine-playback-{lane}")
        self._jobs[lane] = task

        def completed(done: asyncio.Task[None]) -> None:
            if not done.cancelled() and (error := done.exception()) is not None:
                _LOGGER.error("engine.playback.effect_failed", exc_info=error)

        task.add_done_callback(completed)

    def apply(self, effects: tuple[Effect, ...]) -> None:
        for effect in effects:
            if not isinstance(effect, SetVolume):
                _LOGGER.info(
                    "engine.playback.effect_dispatched effect=%s attempt=%s preparation=%s",
                    type(effect).__name__,
                    getattr(effect, "attempt_id", None),
                    getattr(effect, "preparation_id", None),
                )
            match effect:
                case StartAttempt():
                    self._replace("output", partial(self._start, effect))
                case StopOutput():
                    self._replace(
                        "output", partial(self._stop, effect.attempt_id), cleanup=True
                    )
                case Connect():
                    self._replace("voice", partial(self._connect, effect))
                case Disconnect():
                    self._replace(
                        "voice",
                        partial(self._disconnect, effect.connection_id),
                        cleanup=True,
                    )
                case PrepareNext():
                    self._replace("preparation", partial(self._prepare, effect))
                case DiscardNext():
                    self._replace(
                        "preparation",
                        partial(self._discard, effect.preparation_id),
                        cleanup=True,
                    )
                case ChangePause():
                    try:
                        (self.audio.pause if effect.paused else self.audio.resume)(
                            effect.attempt_id
                        )
                    except Exception:
                        self.notify(AttemptFailed(effect.attempt_id))
                case SetVolume():
                    self.audio.set_volume(effect.volume)
                case Activate():
                    try:
                        activated = self.audio.start_transition(
                            outgoing_attempt_id=effect.outgoing_attempt_id,
                            preparation_id=effect.preparation_id,
                            attempt_id=effect.attempt_id,
                            notify=self.notify,
                        )
                    except Exception:
                        activated = False
                    if not activated:
                        self.notify(
                            TransitionFailed(effect.attempt_id, effect.preparation_id)
                        )

    async def _start(self, effect: StartAttempt) -> None:
        try:
            if progress := self.audio.progress:
                await self.audio.stop(progress.attempt_id)
            started_at = time.monotonic()
            _LOGGER.info(
                "engine.playback.resolving attempt=%s track_id=%s",
                effect.attempt_id,
                effect.track_id,
            )
            source = await self._catalog.resolve_audio(effect.track_id)
            _LOGGER.info(
                "engine.playback.resolved attempt=%s elapsed=%.3f duration=%s",
                effect.attempt_id,
                time.monotonic() - started_at,
                source.track.metadata.duration_seconds,
            )
            accepted = await self._report(
                SourceResolved(
                    effect.attempt_id, source.track.metadata.duration_seconds
                )
            )
            if (
                accepted.playback.attempt_id != effect.attempt_id
                or accepted.playback.phase is not PlaybackPhase.STARTING
            ):
                return
            _LOGGER.info(
                "engine.playback.starting attempt=%s position=%.3f paused=%s",
                effect.attempt_id,
                accepted.checkpoint.position_seconds,
                accepted.checkpoint.intent is PlaybackIntent.PAUSED,
            )
            await self.audio.play(
                source,
                effect.attempt_id,
                self.notify,
                position_seconds=accepted.checkpoint.position_seconds,
                paused=accepted.checkpoint.intent is PlaybackIntent.PAUSED,
            )
            # Pause may have committed while an adapter was establishing output.
            current = self._snapshot()
            if current.playback.attempt_id == effect.attempt_id:
                (
                    self.audio.pause
                    if current.checkpoint.intent is PlaybackIntent.PAUSED
                    else self.audio.resume
                )(effect.attempt_id)
        except asyncio.CancelledError:
            await self.audio.stop(effect.attempt_id)
            raise
        except Exception:
            _LOGGER.exception(
                "engine.playback.start_failed",
                extra={"attempt_id": str(effect.attempt_id)},
            )
            self.notify(AttemptFailed(effect.attempt_id))

    async def _stop(self, attempt_id: UUID) -> None:
        task = asyncio.create_task(self.audio.stop(attempt_id))
        try:
            await asyncio.shield(task)
        finally:
            await task

    async def _connect(self, effect: Connect) -> None:
        try:
            if connection := self.voice.connection:
                await self.voice.disconnect(connection.connection_id)
            await self.voice.connect(effect.channel_id, effect.connection_id)
            await self._report(
                Joined(VoiceConnection(effect.connection_id, effect.channel_id))
            )
        except asyncio.CancelledError:
            await self.voice.disconnect(effect.connection_id)
            raise
        except Exception:
            _LOGGER.exception("engine.playback.join_failed")
            self.notify(JoinFailed(effect.connection_id))

    async def _disconnect(self, connection_id: UUID) -> None:
        task = asyncio.create_task(self.voice.disconnect(connection_id))
        try:
            await asyncio.shield(task)
        finally:
            await task

    async def _prepare(self, effect: PrepareNext) -> None:
        try:
            started_at = time.monotonic()
            _LOGGER.info(
                "engine.playback.preparing preparation=%s outgoing=%s track_id=%s",
                effect.preparation_id,
                effect.outgoing_attempt_id,
                effect.track_id,
            )
            source = await self._catalog.resolve_audio(effect.track_id)
            duration = source.track.metadata.duration_seconds
            accepted = (
                duration is not None
                and duration > 0
                and await self.audio.prepare_next(
                    source,
                    outgoing_attempt_id=effect.outgoing_attempt_id,
                    preparation_id=effect.preparation_id,
                    seconds=min(effect.seconds, duration / 2),
                    notify=self.notify,
                )
            )
            _LOGGER.info(
                "engine.playback.prepared preparation=%s accepted=%s elapsed=%.3f",
                effect.preparation_id,
                accepted,
                time.monotonic() - started_at,
            )
            await self._report(Prepared(effect.preparation_id, accepted))
        except asyncio.CancelledError:
            await self.audio.discard_next(effect.preparation_id)
            raise
        except Exception:
            _LOGGER.exception("engine.playback.prepare_failed")
            self.notify(Prepared(effect.preparation_id, False))

    async def _discard(self, preparation_id: UUID) -> None:
        task = asyncio.create_task(self.audio.discard_next(preparation_id))
        try:
            await asyncio.shield(task)
        finally:
            await task

    async def _checkpoints(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self.audio.progress and self._accepting:
                try:
                    await self._report(Control.CHECKPOINT)
                except Exception:
                    _LOGGER.exception("engine.playback.checkpoint_failed")

    def freeze(self) -> None:
        self._accepting = False
        self._ticker.cancel()
        if progress := self.audio.progress:
            self.audio.pause(progress.attempt_id)

    async def close(self) -> None:
        self.freeze()
        for task in self._jobs.values():
            task.cancel()
        await asyncio.gather(
            self._ticker,
            *self._jobs.values(),
            *self._deliveries,
            return_exceptions=True,
        )
        if progress := self.audio.progress:
            await self.audio.stop(progress.attempt_id)
        if connection := self.voice.connection:
            await self.voice.disconnect(connection.connection_id)

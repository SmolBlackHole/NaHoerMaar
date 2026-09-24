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

from .audio import AudioPlayer, AudioSourceNotReady, VoiceConnection, VoiceTransport
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
from .logs import current_actor, current_trace_id, log_context, request_trace_id

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

    def notify(
        self,
        event: PlaybackMessage,
        *,
        trace_id: str | None = None,
        actor_id: str | None = None,
        actor_name: str | None = None,
    ) -> None:
        # Called by Discord's audio thread as well as by asyncio tasks.
        if self._accepting and not self._loop.is_closed():
            identifier = trace_id or current_trace_id() or request_trace_id()
            context_actor_id, context_actor_name = current_actor()
            resolved_actor_id = actor_id or context_actor_id
            resolved_actor_name = actor_name or context_actor_name
            with log_context(
                resolved_actor_id, resolved_actor_name, trace_id=identifier
            ):
                _LOGGER.debug(
                    "engine.playback.fact_received fact=%s attempt=%s preparation=%s",
                    type(event).__name__,
                    getattr(event, "attempt_id", None),
                    getattr(event, "preparation_id", None),
                )
            self._loop.call_soon_threadsafe(
                self._deliver,
                event,
                identifier,
                resolved_actor_id,
                resolved_actor_name,
            )

    def _deliver(
        self,
        event: PlaybackMessage,
        trace_id: str,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        if not self._accepting:
            return

        async def run() -> None:
            with log_context(actor_id, actor_name, trace_id=trace_id):
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
        _LOGGER.debug(
            "engine.playback.effect_started lane=%s cleanup=%s", lane, cleanup
        )

        def completed(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                _LOGGER.debug("engine.playback.effect_cancelled lane=%s", lane)
            elif (error := done.exception()) is not None:
                _LOGGER.error("engine.playback.effect_failed", exc_info=error)
            else:
                _LOGGER.debug("engine.playback.effect_completed lane=%s", lane)

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
                    except Exception as error:
                        _LOGGER.error(
                            "engine.playback.pause_change_failed attempt=%s paused=%s",
                            effect.attempt_id,
                            effect.paused,
                            exc_info=error,
                        )
                        self.notify(AttemptFailed(effect.attempt_id))
                case SetVolume():
                    self.audio.set_volume(effect.volume)
                case Activate():
                    try:
                        trace_id = current_trace_id() or request_trace_id()
                        actor_id, actor_name = current_actor()
                        activated = self.audio.start_transition(
                            outgoing_attempt_id=effect.outgoing_attempt_id,
                            preparation_id=effect.preparation_id,
                            attempt_id=effect.attempt_id,
                            notify=partial(
                                self.notify,
                                trace_id=trace_id,
                                actor_id=actor_id,
                                actor_name=actor_name,
                            ),
                        )
                    except Exception as error:
                        _LOGGER.error(
                            "engine.playback.transition_start_failed attempt=%s "
                            "preparation=%s",
                            effect.attempt_id,
                            effect.preparation_id,
                            exc_info=error,
                        )
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
            actor_id, actor_name = current_actor()
            await self.audio.play(
                source,
                effect.attempt_id,
                partial(
                    self.notify,
                    trace_id=current_trace_id() or request_trace_id(),
                    actor_id=actor_id,
                    actor_name=actor_name,
                ),
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
            _LOGGER.info(
                "engine.playback.start_cancelled attempt=%s track_id=%s",
                effect.attempt_id,
                effect.track_id,
            )
            await self.audio.stop(effect.attempt_id)
            raise
        except AudioSourceNotReady:
            _LOGGER.warning(
                "engine.playback.source_unready attempt=%s track_id=%s",
                effect.attempt_id,
                effect.track_id,
            )
            self.notify(AttemptFailed(effect.attempt_id, retryable=True))
        except Exception:
            _LOGGER.exception(
                "engine.playback.start_failed",
                extra={"attempt_id": str(effect.attempt_id)},
            )
            self.notify(AttemptFailed(effect.attempt_id))

    async def _stop(self, attempt_id: UUID) -> None:
        _LOGGER.info("engine.playback.stopping attempt=%s", attempt_id)
        task = asyncio.create_task(self.audio.stop(attempt_id))
        try:
            await asyncio.shield(task)
        finally:
            await task
        _LOGGER.info("engine.playback.stopped attempt=%s", attempt_id)

    async def _connect(self, effect: Connect) -> None:
        started_at = time.monotonic()
        _LOGGER.info(
            "engine.playback.joining connection=%s channel_id=%s",
            effect.connection_id,
            effect.channel_id,
        )
        try:
            if connection := self.voice.connection:
                await self.voice.disconnect(connection.connection_id)
            await self.voice.connect(effect.channel_id, effect.connection_id)
            await self._report(
                Joined(VoiceConnection(effect.connection_id, effect.channel_id))
            )
            _LOGGER.info(
                "engine.playback.joined connection=%s channel_id=%s elapsed=%.3f",
                effect.connection_id,
                effect.channel_id,
                time.monotonic() - started_at,
            )
        except asyncio.CancelledError:
            _LOGGER.info(
                "engine.playback.join_cancelled connection=%s channel_id=%s",
                effect.connection_id,
                effect.channel_id,
            )
            await self.voice.disconnect(effect.connection_id)
            raise
        except Exception:
            _LOGGER.exception("engine.playback.join_failed")
            self.notify(JoinFailed(effect.connection_id))

    async def _disconnect(self, connection_id: UUID) -> None:
        _LOGGER.info("engine.playback.disconnecting connection=%s", connection_id)
        task = asyncio.create_task(self.voice.disconnect(connection_id))
        try:
            await asyncio.shield(task)
        finally:
            await task
        _LOGGER.info("engine.playback.disconnected connection=%s", connection_id)

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
            actor_id, actor_name = current_actor()
            accepted = (
                duration is not None
                and duration > 0
                and await self.audio.prepare_next(
                    source,
                    outgoing_attempt_id=effect.outgoing_attempt_id,
                    preparation_id=effect.preparation_id,
                    seconds=min(effect.seconds, duration / 2),
                    notify=partial(
                        self.notify,
                        trace_id=current_trace_id() or request_trace_id(),
                        actor_id=actor_id,
                        actor_name=actor_name,
                    ),
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
            _LOGGER.info(
                "engine.playback.prepare_cancelled preparation=%s track_id=%s",
                effect.preparation_id,
                effect.track_id,
            )
            await self.audio.discard_next(effect.preparation_id)
            raise
        except Exception:
            _LOGGER.exception("engine.playback.prepare_failed")
            self.notify(Prepared(effect.preparation_id, False))

    async def _discard(self, preparation_id: UUID) -> None:
        _LOGGER.debug(
            "engine.playback.discarding_preparation preparation=%s", preparation_id
        )
        task = asyncio.create_task(self.audio.discard_next(preparation_id))
        try:
            await asyncio.shield(task)
        finally:
            await task
        _LOGGER.debug(
            "engine.playback.preparation_discarded preparation=%s", preparation_id
        )

    async def _checkpoints(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self.audio.progress and self._accepting:
                try:
                    await self._report(Control.CHECKPOINT)
                except Exception:
                    _LOGGER.exception("engine.playback.checkpoint_failed")

    def freeze(self) -> None:
        _LOGGER.info("engine.playback.freezing jobs=%s", len(self._jobs))
        self._accepting = False
        self._ticker.cancel()
        if progress := self.audio.progress:
            self.audio.pause(progress.attempt_id)

    async def close(self) -> None:
        started_at = time.monotonic()
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
        _LOGGER.info(
            "engine.playback.closed elapsed=%.3f", time.monotonic() - started_at
        )

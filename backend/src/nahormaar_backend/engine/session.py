# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""One ordered mutation boundary, with effects and subscribers outside transactions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .catalog import Catalog
from .audio import AudioPlayer, VoiceTransport
from .domain.playback import (
    Control,
    Effect,
    Join,
    Seek,
    SetVolume,
    SetCrossfade,
    PlaybackCommand,
    PlaybackMessage,
    decide,
)
from nahormaar_backend.domain.identity import Contributor
from .domain.queue import (
    Add,
    Clear,
    Move,
    Outcome,
    Queue,
    QueueCommand,
    QueueOrigin,
    Remove,
    Undo,
    UndoUnavailable,
)
from .domain.radio import (
    RADIO_POOL,
    ManualStrategy,
    RadioLoaded,
    RadioRequest,
    RadioState,
    RadioStrategy,
    RetryRadio,
    StartRadio,
    StopRadio,
)
from .domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackIntent,
    PlaybackPhase,
    PlaybackRuntime,
    Receipt,
    SessionChanged,
    SessionAction,
    SessionSnapshot,
)
from .events import EventBus
from .playback import PlaybackController
from .persistence import (
    ListeningSessionRepository,
    OperationRepository,
    PlaybackCheckpointRepository,
    PlaybackRecordRepository,
    QueueRepository,
    RadioStrategyRepository,
    TrackRepository,
    write_transaction,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefillRadio:
    pass


type Command = QueueCommand | StartRadio | StopRadio | RetryRadio | PlaybackCommand
type Message = Command | RefillRadio | RadioLoaded | PlaybackMessage


def action_for(message: Message) -> SessionAction:
    """Public action identity, independent of implementation class names."""
    if isinstance(message, Control):
        controls: dict[Control, SessionAction] = {
            Control.PLAY: "playback.play",
            Control.PAUSE: "playback.pause",
            Control.SKIP: "playback.skip",
            Control.STOP: "playback.stop",
            Control.LEAVE: "connection.leave",
        }
        return controls.get(message, "session.updated")
    actions: dict[type, SessionAction] = {
        Add: "queue.added",
        Remove: "queue.removed",
        Move: "queue.reordered",
        Clear: "queue.cleared",
        Undo: "queue.restored",
        Seek: "playback.seek",
        SetVolume: "playback.volume",
        SetCrossfade: "playback.crossfade",
        Join: "connection.join",
        StartRadio: "radio.started",
        StopRadio: "radio.stopped",
        RetryRadio: "radio.retried",
    }
    return actions.get(type(message), "session.updated")


@dataclass(frozen=True, slots=True)
class Reply:
    snapshot: SessionSnapshot
    outcome: Outcome
    replayed: bool = False


@dataclass(slots=True)
class _Envelope:
    message: Message
    result: asyncio.Future[Reply]
    request_id: UUID | None = None
    actor: Contributor | None = None
    expected_attempt_id: UUID | None = None


class Session:
    """Only the inbox runner replaces committed state. Providers are borrowed."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        snapshot: SessionSnapshot,
        *,
        catalog: Catalog | None,
        clock: Callable[[], datetime],
    ) -> None:
        self._sessions, self._state, self._catalog, self._clock = (
            sessions,
            snapshot,
            catalog,
            clock,
        )
        self.events: EventBus[SessionChanged] = EventBus()
        self._inbox: asyncio.Queue[_Envelope | None] = asyncio.Queue(128)
        self._accepting = True
        self._radio_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._runner = asyncio.create_task(self._run(), name="engine-session")
        self._radio_subscriber: asyncio.Task[None] | None = None
        self._playback: PlaybackController | None = None

    @classmethod
    async def open(
        cls,
        sessions: async_sessionmaker[AsyncSession],
        session_id: UUID,
        *,
        clock: Callable[[], datetime],
        catalog: Catalog | None = None,
        audio: AudioPlayer | None = None,
        voice: VoiceTransport | None = None,
    ) -> Session:
        if (audio is None) != (voice is None) or (
            audio is not None and catalog is None
        ):
            raise ValueError(
                "Playback needs a catalog, audio player and voice transport."
            )
        async with write_transaction(sessions) as database:
            settings = await ListeningSessionRepository(database).get(session_id)
            if settings is None:
                settings = ListeningSession(id=session_id)
                await ListeningSessionRepository(database).add(settings)
            queue = Queue(
                session_id, await QueueRepository(database).entries(session_id)
            )
            checkpoint = await PlaybackCheckpointRepository(database).get(
                session_id
            ) or PlaybackCheckpoint(session_id)
            strategy = await RadioStrategyRepository(database).get(session_id)
            if strategy is not None and strategy.state is RadioState.LOADING:
                strategy = replace(
                    strategy, state=RadioState.ACTIVE, request_id=None, error=None
                )
                await RadioStrategyRepository(database).save(session_id, strategy)
            history = await PlaybackRecordRepository(database).recent(session_id)
            if checkpoint.play_id and not any(
                record.id == checkpoint.play_id for record in history
            ):
                current_record = await PlaybackRecordRepository(database).get(
                    session_id, checkpoint.play_id
                )
                if current_record is None or current_record.ended_at is not None:
                    raise ValueError("Recovery checkpoint has no open playback record.")
                history = (*history, current_record)
            if any(
                record.id == checkpoint.play_id and record.ended_at is not None
                for record in history
            ):
                raise ValueError(
                    "Recovery checkpoint references a finished playback record."
                )
        owner = cls(
            sessions,
            SessionSnapshot(
                settings,
                queue,
                checkpoint,
                history,
                strategy or ManualStrategy(),
                playback=PlaybackRuntime(phase=PlaybackPhase.SUSPENDED)
                if checkpoint.track_id
                else PlaybackRuntime(),
            ),
            catalog=catalog,
            clock=clock,
        )
        if catalog is not None and audio is not None and voice is not None:
            owner._playback = PlaybackController(
                catalog,
                audio,
                voice,
                report=owner.report,
                snapshot=lambda: owner.snapshot,
            )
        ready = asyncio.Event()
        owner._radio_subscriber = asyncio.create_task(
            owner._observe(ready), name="engine-radio-events"
        )
        try:
            await ready.wait()
            if owner._playback:
                owner._playback.audio.set_volume(settings.volume)
                await owner._submit(Control.RESTORE)
            if catalog is not None and strategy is not None:
                await owner._submit(RefillRadio())
        except BaseException:
            await owner.close()
            raise
        return owner

    @property
    def snapshot(self) -> SessionSnapshot:
        return self._state

    async def request(
        self,
        request_id: UUID,
        command: Command,
        *,
        actor: Contributor | None = None,
        expected_attempt_id: UUID | None = None,
    ) -> Reply:
        return await self._submit(
            command,
            request_id=request_id,
            actor=actor,
            expected_attempt_id=expected_attempt_id,
        )

    async def report(self, message: PlaybackMessage) -> SessionSnapshot:
        """Technical facts use the same inbox, with backpressure instead of loss."""
        return (await self._submit(message, wait_for_space=True)).snapshot

    async def _submit(
        self,
        message: Message,
        *,
        request_id: UUID | None = None,
        actor: Contributor | None = None,
        wait_for_space: bool = False,
        expected_attempt_id: UUID | None = None,
    ) -> Reply:
        if not self._accepting:
            raise RuntimeError("Session is closing.")
        result: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        result.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        try:
            envelope = _Envelope(
                message, result, request_id, actor, expected_attempt_id
            )
            if wait_for_space:
                await self._inbox.put(envelope)
            else:
                self._inbox.put_nowait(envelope)
        except asyncio.QueueFull:
            raise RuntimeError("Session is busy. Try again shortly.") from None
        return await asyncio.shield(result)

    async def _run(self) -> None:
        while (envelope := await self._inbox.get()) is not None:
            try:
                reply = await self._execute(envelope)
            except Exception as exc:
                _LOGGER.error(
                    "engine.session.command_failed command=%s request=%s error=%s",
                    type(envelope.message).__name__,
                    envelope.request_id,
                    type(exc).__name__,
                )
                envelope.result.set_exception(exc)
            else:
                envelope.result.set_result(reply)
            finally:
                self._inbox.task_done()

    async def _execute(self, envelope: _Envelope) -> Reply:
        before = self._state
        after = before
        queue, strategy = before.queue, before.strategy
        message, actor = envelope.message, envelope.actor
        session_id = before.settings.id
        fingerprint = (
            json.dumps(
                [
                    type(message).__name__,
                    message.value if isinstance(message, Control) else asdict(message),
                ],
                sort_keys=True,
                default=str,
            )
            if envelope.request_id is not None
            else ""
        )
        actor_id = actor.id if actor else None
        if envelope.expected_attempt_id is not None:
            fingerprint += f":attempt={envelope.expected_attempt_id}"
        captured = None
        consume = None
        request = None
        effects: tuple[Effect, ...] = ()
        outcome = Outcome(actor=actor)
        excluded = frozenset(record.track_id for record in before.history) | frozenset(
            (before.checkpoint.track_id,) if before.checkpoint.track_id else ()
        )
        async with write_transaction(self._sessions) as database:
            operations = OperationRepository(database)
            if envelope.request_id is not None:
                previous = await operations.get(envelope.request_id)
                if previous:
                    if (
                        previous.session_id,
                        previous.actor_id,
                        previous.fingerprint,
                    ) != (session_id, actor_id, fingerprint):
                        return Reply(before, Outcome("idempotency_conflict"), False)
                    return Reply(before, previous.outcome, True)
            try:
                if (
                    envelope.expected_attempt_id is not None
                    and envelope.expected_attempt_id != before.playback.attempt_id
                ):
                    outcome = Outcome("playback_conflict", actor=actor)
                elif (
                    isinstance(message, (Move, Clear))
                    and message.expected_queue_revision
                    != before.settings.queue_revision
                ):
                    outcome = Outcome("queue_conflict", actor=actor)
                elif (
                    isinstance(message, (StartRadio, StopRadio, RetryRadio))
                    and message.expected_generation != strategy.generation
                ):
                    outcome = Outcome("radio_conflict", actor=actor)
                elif isinstance(message, (Add, Remove, Move, Clear, Undo)):
                    removal = (
                        await operations.removal(
                            session_id, message.undo_id, now=self._clock()
                        )
                        if isinstance(message, Undo)
                        else None
                    )
                    if isinstance(message, Add):
                        tracks = TrackRepository(database)
                        for track_id in set(message.track_ids):
                            if await tracks.get(track_id) is None:
                                raise ValueError("Unknown catalog track.")
                    if removal and any(
                        entry.id == before.checkpoint.entry_id
                        for group in removal.groups
                        for entry in group.entries
                    ):
                        raise UndoUnavailable("This entry is already playing.")
                    queue, outcome, captured = queue.edit(
                        message,
                        actor=actor,
                        now=self._clock(),
                        removal=removal,
                        current_track_id=before.checkpoint.track_id,
                    )
                    if isinstance(message, Undo):
                        consume = message.undo_id
                    if isinstance(strategy, RadioStrategy) and outcome.removed_count:
                        strategy = replace(
                            strategy,
                            excluded=strategy.excluded
                            | {entry.track_id for entry in outcome.entries},
                        )
                    if isinstance(message, Clear) and message.contributor_id is None:
                        strategy = ManualStrategy()
                elif isinstance(message, StartRadio):
                    if actor is None or self._catalog is None:
                        raise ValueError("Radio needs an actor and a catalog.")
                    strategy = RadioStrategy(
                        message.seed, actor, pool=message.initial_tracks[:RADIO_POOL]
                    )
                elif isinstance(message, StopRadio):
                    strategy = ManualStrategy()
                elif isinstance(message, RetryRadio):
                    if (
                        not isinstance(strategy, RadioStrategy)
                        or strategy.state is not RadioState.WAITING
                    ):
                        raise ValueError("Radio is not waiting for retry.")
                    strategy = replace(strategy, state=RadioState.ACTIVE, error=None)
                elif isinstance(message, RadioLoaded):
                    if isinstance(strategy, RadioStrategy):
                        strategy = strategy.loaded(
                            message,
                            excluded | {entry.track_id for entry in queue.entries},
                        )
                elif isinstance(message, RefillRadio):
                    strategy, selected, request = strategy.queue_next(
                        queue.entries,
                        excluded,
                        paused=before.checkpoint.intent is PlaybackIntent.PAUSED,
                    )
                    if selected and isinstance(strategy, RadioStrategy):
                        queue, outcome, _ = queue.edit(
                            Add(selected, True, QueueOrigin.RADIO),
                            actor=strategy.initiator,
                            now=self._clock(),
                            current_track_id=before.checkpoint.track_id,
                        )
                else:
                    if self._playback is None:
                        raise ValueError("Playback is not configured.")
                    decision = decide(
                        before,
                        message,
                        now=self._clock(),
                        progress=self._playback.audio.progress,
                        connection=self._playback.voice.connection,
                    )
                    after, effects = decision.snapshot, decision.effects
                    queue, strategy = after.queue, after.strategy
                    outcome = Outcome(decision.code, actor=actor)
            except UndoUnavailable:
                outcome = Outcome("undo_unavailable", actor=actor)
            except KeyError:
                outcome = Outcome("entry_not_found", actor=actor)
            except ValueError:
                outcome = Outcome("invalid_action", actor=actor)
            if outcome.code != "ok":
                after, effects = before, ()
                queue, strategy, captured, consume, request = (
                    before.queue,
                    before.strategy,
                    None,
                    None,
                    None,
                )
            after = replace(after, queue=queue, strategy=strategy)
            if self._playback and (
                queue != before.queue or strategy != before.strategy
            ):
                reconciled = decide(
                    after,
                    Control.RECONCILE,
                    now=self._clock(),
                    progress=self._playback.audio.progress,
                    connection=self._playback.voice.connection,
                )
                after = reconciled.snapshot
                effects += reconciled.effects
                queue, strategy = after.queue, after.strategy
            queue_changed = queue != before.queue
            changed = after != before
            settings = replace(
                after.settings,
                revision=before.settings.revision + int(changed),
                queue_revision=before.settings.queue_revision + int(queue_changed),
            )
            after = replace(after, settings=settings)
            if changed:
                if queue_changed:
                    await QueueRepository(database).replace(session_id, queue.entries)
                await ListeningSessionRepository(database).update(
                    settings, expected_revision=before.settings.revision
                )
                previous_records = {record.id: record for record in before.history}
                records = PlaybackRecordRepository(database)
                for record in after.history:
                    if record.id not in previous_records:
                        await records.add(record)
                    elif record != previous_records[record.id]:
                        await records.update(record)
                if after.checkpoint != before.checkpoint:
                    await PlaybackCheckpointRepository(database).save(after.checkpoint)
                if strategy != before.strategy:
                    radio = RadioStrategyRepository(database)
                    if isinstance(strategy, RadioStrategy):
                        await radio.save(session_id, strategy)
                    else:
                        await radio.clear(session_id)
            if envelope.request_id is not None:
                await operations.add(
                    Receipt(
                        envelope.request_id, session_id, fingerprint, actor_id, outcome
                    ),
                    removal=captured,
                    consume=consume,
                )
        self._state = after
        action = action_for(message)
        if (
            before.playback.phase != after.playback.phase
            or before.playback.connection_id != after.playback.connection_id
            or before.checkpoint.track_id != after.checkpoint.track_id
            or before.checkpoint.intent != after.checkpoint.intent
        ):
            _LOGGER.info(
                "engine.session.transition phase=%s->%s intent=%s->%s "
                "track=%s->%s connected=%s->%s",
                before.playback.phase,
                after.playback.phase,
                before.checkpoint.intent,
                after.checkpoint.intent,
                before.checkpoint.track_id,
                after.checkpoint.track_id,
                before.playback.connection_id is not None,
                after.playback.connection_id is not None,
            )
        if action != "session.updated":
            _LOGGER.info(
                "engine.session.action action=%s outcome=%s request=%s "
                "revision=%s queue_revision=%s queue_size=%s",
                action,
                outcome.code,
                envelope.request_id,
                after.settings.revision,
                after.settings.queue_revision,
                len(after.queue.entries),
            )
        elif queue_changed:
            _LOGGER.info(
                "engine.session.queue_updated source=%s queue_revision=%s size=%s",
                type(message).__name__,
                after.settings.queue_revision,
                len(after.queue.entries),
            )
        if changed:
            self.events.publish(
                SessionChanged(
                    before,
                    after,
                    action,
                    outcome,
                    envelope.request_id,
                )
            )
        if self._playback and self._accepting:
            self._playback.apply(effects)
        if (
            strategy.generation != before.strategy.generation
            and self._radio_task is not None
        ):
            self._radio_task.cancel()
        if request is not None and self._accepting:
            previous_task = self._radio_task
            self._radio_task = asyncio.create_task(
                self._fetch_radio(request, previous_task), name="engine-radio-fetch"
            )
        return Reply(after, outcome)

    async def _observe(self, ready: asyncio.Event) -> None:
        async with self.events.subscribe(capacity=1, latest=True) as events:
            ready.set()
            while (event := await events.get()) is not None:
                if not self._accepting:
                    return
                if (
                    event.before.queue == event.after.queue
                    and event.before.strategy == event.after.strategy
                    and event.before.checkpoint.track_id
                    == event.after.checkpoint.track_id
                    and event.before.checkpoint.intent == event.after.checkpoint.intent
                ):
                    continue
                try:
                    await self._submit(RefillRadio())
                except Exception:
                    _LOGGER.exception("engine.radio.refill_command_failed")

    async def _fetch_radio(
        self, request: RadioRequest, previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        if self._catalog is None:
            return
        _LOGGER.info(
            "engine.radio.fetching request=%s generation=%s",
            request.id,
            request.generation,
        )
        try:
            candidates = await self._catalog.radio_next(
                request.seed, limit=RADIO_POOL, continuation=request.continuation
            )
            _LOGGER.info(
                "engine.radio.fetched request=%s count=%s has_more=%s error=%s",
                request.id,
                len(candidates.tracks),
                candidates.continuation is not None,
                candidates.error is not None,
            )
            result = RadioLoaded(
                request.generation,
                request.id,
                tuple(track.id for track in candidates.tracks),
                candidates.continuation,
                candidates.error,
            )
        except Exception as error:
            _LOGGER.warning(
                "engine.radio.fetch_failed request=%s error=%s",
                request.id,
                type(error).__name__,
            )
            result = RadioLoaded(
                request.generation,
                request.id,
                (),
                error="Radio could not load more tracks. Try again.",
            )
        if self._accepting:
            try:
                await self._submit(result)
            except Exception:
                _LOGGER.exception("engine.radio.result_commit_failed")

    async def close(self) -> None:
        if self._close_task is None:
            self._accepting = False
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        if self._radio_task is not None:
            self._radio_task.cancel()
            await asyncio.gather(self._radio_task, return_exceptions=True)
        self.events.close()
        if self._radio_subscriber is not None:
            self._radio_subscriber.cancel()
            await asyncio.gather(self._radio_subscriber, return_exceptions=True)
        await self._inbox.join()
        try:
            if self._playback:
                self._playback.freeze()
                result: asyncio.Future[Reply] = (
                    asyncio.get_running_loop().create_future()
                )
                await self._inbox.put(_Envelope(Control.QUIESCE, result))
                await result
        finally:
            try:
                if self._playback:
                    await self._playback.close()
            finally:
                await self._inbox.put(None)
                await self._runner

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Compose one music Session with ordered commands and committed notifications."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, replace
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from enum import Enum, auto
from math import isfinite
from typing import cast
from uuid import UUID

from ..domain import commands
from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.commands import Outcome, Receipt, Revisions
from ..domain.undo import UndoUnavailable
from ..domain.models import (
    Contributor,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from .audio import (
    AudioCompleted,
    AudioStarted,
    VoiceDisconnected,
    MetadataResolver,
    SourceResolver,
    VoiceChannelInfo,
    VoiceError,
    VoiceOutput,
)
from .catalog import MediaCatalog, CatalogEntries, source_key
from .metadata import QueueMetadata
from .events import (
    PlaybackChanged,
    QueueChanged,
    RadioChanged,
    SessionEvent,
    SessionEvents,
    Snapshots,
    TrackStarted,
)
from .playback import (
    CrossfadeReady,
    FadeFinished,
    PlaybackController,
    PlaybackMessage,
    PreparationFailed,
    SourceFailed,
    SourceReady,
)
from .player import Player
from .queue import Enqueue, Queue
from .radio import RadioCatalog, RadioController, RadioLoaded, RadioMessage, RefillRadio
from .status import CommandReply, PlaybackStatus
from .storage import PlayerStore, StorageError
from .worker import PlayerWorker

CHECKPOINT_INTERVAL_SECONDS = 5.0
INBOX_CAPACITY = 128
CALLBACK_RESERVE = 16
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Request:
    request_id: UUID
    command: commands.Command
    actor_id: UUID | None = None
    actor: Contributor | None = None


@dataclass(frozen=True, slots=True)
class MetadataResolved:
    entry_id: UUID
    values: TrackMetadata


class SessionAction(Enum):
    READ = auto()
    RESTORE = auto()
    CHECKPOINT = auto()
    PREPARE = auto()


type SessionMessage = (
    Request
    | commands.Command
    | Enqueue
    | PlaybackMessage
    | RadioMessage
    | MetadataResolved
    | SessionAction
)
type SessionResult = PlayerSnapshot | PlaybackStatus | CommandReply | None


@dataclass(frozen=True, slots=True)
class PendingMessage:
    message: SessionMessage
    result: asyncio.Future[SessionResult]


class SessionState:
    def __init__(
        self, worker: PlayerWorker, snapshot: PlayerSnapshot, revisions: Revisions
    ) -> None:
        self.worker = worker
        self.snapshot = snapshot
        self.revisions = revisions
        self.closing = False
        self.faulted = False

    async def change(self, operation: Callable[[Player], PlayerSnapshot]) -> None:
        self.snapshot = await self.worker.call(operation)


class Session:
    """One awaited inbox owns mutations; components own their algorithms."""

    def __init__(
        self,
        state: SessionState,
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        metadata_resolver: MetadataResolver | None = None,
        catalog: MediaCatalog | None = None,
        radio_catalog: RadioCatalog | None = None,
        checkpoint: PlaybackCheckpoint | None = None,
    ) -> None:
        self.state = state
        self.voice = voice
        self.resolver = resolver
        self.radio_catalog = radio_catalog
        self._inbox: deque[PendingMessage] = deque()
        self._ready = asyncio.Event()
        self._space = asyncio.Event()
        self._space.set()
        self._overflow = False
        self._close_task: asyncio.Task[None] | None = None
        self.playback = PlaybackController(
            state,
            resolver,
            voice,
            post=self._post,
            stop_radio=lambda: self.radio.stop(),
            checkpoint=checkpoint,
        )
        entries = CatalogEntries(
            catalog=catalog, radio=radio_catalog, snapshot=lambda: self.snapshot
        )
        self.queue = Queue(
            snapshot=lambda: self.snapshot,
            entry=entries.queue_entry,
            source_key=source_key,
            change=state.change,
            removal=lambda identifier, actor: state.worker.call(
                lambda player: player.removal(identifier, actor)
            ),
        )
        self.radio = RadioController(
            radio_catalog.provider if radio_catalog else None,
            snapshot=lambda: self.snapshot,
            available=lambda: not state.closing and not state.faulted,
            source_key=source_key,
            send=self._send_radio,
            enqueue=lambda entries: state.change(
                lambda player: player.enqueue_many(entries)
            ),
            connected=lambda: voice.connected,
            play=self.playback.play,
        )
        self.requests = SessionRequests(self)
        self._published = self.status
        self._events: Snapshots[PlaybackStatus] = Snapshots()
        self.events = SessionEvents()
        self._metadata = (
            QueueMetadata(
                metadata_resolver,
                snapshot=lambda: self.snapshot,
                active=lambda: not state.closing and not state.faulted,
                needs_refresh=lambda source: (
                    catalog is not None and catalog.needs_refresh(source)
                ),
                apply=self._apply_metadata,
            )
            if metadata_resolver is not None
            else None
        )
        # These consumers recompute current state, so only the latest pending
        # invalidation is needed. Ordinary fact subscribers retain FIFO delivery.
        self.events.subscribe(
            (QueueChanged,), self._refresh_metadata, capacity=1, latest=True
        )
        self.events.subscribe(
            (QueueChanged, PlaybackChanged, RadioChanged),
            self._refill_radio,
            capacity=1,
            latest=True,
        )
        self.events.subscribe(
            (QueueChanged, PlaybackChanged),
            self._prepare_playback,
            capacity=1,
            latest=True,
        )
        self._checkpoint_task: asyncio.Task[None] | None = None
        self._runner = asyncio.create_task(self._run(), name="session-inbox")

    @classmethod
    async def create(
        cls,
        store_factory: Callable[[], PlayerStore],
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        metadata_resolver: MetadataResolver | None = None,
        catalog: MediaCatalog | None = None,
        radio_catalog: RadioCatalog | None = None,
    ) -> Session:
        worker = PlayerWorker(store_factory)
        opening = asyncio.create_task(worker.open())
        try:
            snapshot = await asyncio.shield(opening)
            versions = await worker.call(lambda player: player.revisions)
            checkpoint = await worker.call(lambda player: player.checkpoint)
            session = cls(
                SessionState(worker, snapshot, versions),
                resolver,
                voice,
                metadata_resolver=metadata_resolver,
                catalog=catalog,
                radio_catalog=radio_catalog,
                checkpoint=checkpoint,
            )
            session._checkpoint_task = asyncio.create_task(session._checkpoint_loop())
            return session
        except BaseException:

            async def cleanup() -> None:
                try:
                    await voice.close()
                finally:
                    await worker.close()
                    await asyncio.gather(opening, return_exceptions=True)

            await asyncio.shield(cleanup())
            if opening.done() and not opening.cancelled():
                opening.exception()
            raise

    @property
    def snapshot(self) -> PlayerSnapshot:
        return self.state.snapshot

    @property
    def status(self) -> PlaybackStatus:
        return self.playback.status(self.state.revisions, self.radio.status)

    async def request(
        self,
        request_id: UUID,
        command: commands.Command,
        *,
        actor_id: UUID | None = None,
        actor: Contributor | None = None,
    ) -> CommandReply:
        reply = cast(
            CommandReply,
            await self._send(Request(request_id, command, actor_id, actor)),
        )
        return replace(reply, status=await self.read_status())

    async def restore(self) -> None:
        try:
            await self._send(SessionAction.RESTORE)
        except VoiceError:
            _LOGGER.warning("playback.restore_failed", exc_info=True)

    def channels(self) -> tuple[VoiceChannelInfo, ...]:
        return self.voice.channels()

    async def read_status(self) -> PlaybackStatus:
        return cast(PlaybackStatus, await self._send(SessionAction.READ))

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[PlaybackStatus | None]]:
        await self.read_status()
        self._require_available()
        queue = self._events.subscribe(self._published)
        try:
            yield queue
        finally:
            self._events.unsubscribe(queue)

    def _require_available(self) -> None:
        if self.state.closing:
            raise RuntimeError("Playback controller is closed.")
        if self.state.faulted:
            raise RuntimeError("Playback is halted after an operational failure.")

    async def _send(self, message: SessionMessage) -> SessionResult:
        self._require_available()
        while len(self._inbox) >= INBOX_CAPACITY:
            await self._space.wait()
            self._require_available()
        result = self._accept(message)
        # Cancellation of a caller must not undo an already accepted command.
        return await asyncio.shield(result)

    def _accept(self, message: SessionMessage) -> asyncio.Future[SessionResult]:
        result = asyncio.get_running_loop().create_future()
        result.add_done_callback(self._result_done)
        self._inbox.append(PendingMessage(message, result))
        if len(self._inbox) >= INBOX_CAPACITY:
            self._space.clear()
        self._ready.set()
        return result

    @staticmethod
    def _result_done(result: asyncio.Future[SessionResult]) -> None:
        if not result.cancelled():
            result.exception()

    def _post(self, message: PlaybackMessage) -> None:
        # Playback's thread bridge calls this on the event loop. Reserved slots
        # keep completion callbacks nonblocking even under control backpressure.
        if self.state.closing or self.state.faulted:
            return
        if len(self._inbox) >= INBOX_CAPACITY + CALLBACK_RESERVE:
            _LOGGER.error("session.inbox_overflow message=%s", type(message).__name__)
            self._overflow = True
            self._ready.set()
            return
        self._accept(message)

    async def _run(self) -> None:
        try:
            await self._receive()
        except (Exception, asyncio.CancelledError):
            _LOGGER.exception("session.inbox_failed")
            await self.playback.fault(
                "Session command processing failed; restart the backend.",
                persist_failure=False,
            )
            self._events.close()
        finally:
            while self._inbox:
                self._inbox.popleft().result.set_exception(
                    RuntimeError("Session command processing stopped.")
                )
            self._space.set()

    async def _receive(self) -> None:
        while True:
            await self._ready.wait()
            if self._overflow and not self.state.closing and not self.state.faulted:
                await self.playback.fault(
                    "Playback inbox overloaded; restart the backend.",
                    persist_failure=False,
                )
                self._events.close()
            if not self._inbox:
                self._ready.clear()
                if self.state.closing:
                    return
                continue
            pending = self._inbox.popleft()
            if len(self._inbox) < INBOX_CAPACITY:
                self._space.set()
            try:
                self._require_available()
                result = await self._execute(pending.message)
            except asyncio.CancelledError:
                pending.result.set_exception(
                    RuntimeError("Session command was cancelled; playback halted.")
                )
                raise
            except Exception as exc:
                pending.result.set_exception(exc)
            else:
                pending.result.set_result(result)

    async def _execute(self, message: SessionMessage) -> SessionResult:
        if message is SessionAction.READ:
            return self._published
        self.requests.pending = None
        reply: tuple[Outcome, bool] | None = None
        try:
            try:
                match message:
                    case SessionAction.CHECKPOINT:
                        await self.playback.save_checkpoint()
                        return None
                    case SessionAction.PREPARE:
                        await self.playback.prepare_crossfade()
                        return None
                    case SessionAction.RESTORE:
                        await self.playback.restore()
                    case Request():
                        reply = await self.requests.handle(message)
                    case MetadataResolved(entry_id, values):
                        await self.state.change(
                            lambda player: player.enrich(entry_id, values)
                        )
                    case (
                        SourceReady()
                        | SourceFailed()
                        | AudioCompleted()
                        | AudioStarted()
                        | VoiceDisconnected()
                        | CrossfadeReady()
                        | PreparationFailed()
                        | FadeFinished()
                    ):
                        await self.playback.handle(message)
                    case RefillRadio() | RadioLoaded():
                        await self.radio.handle(message)
                    case _:
                        await self.requests.execute(message)
            except VoiceError as exc:
                await self.playback.voice_failed(str(exc))
                await self._publish()
                raise
            except (StorageError, ValueError, KeyError):
                raise
            except Exception:
                await self.playback.fault(
                    "Playback operation failed; restart the backend."
                )
                await self._publish()
                raise
            await self._publish()
        except StorageError:
            await self.playback.fault(
                "Player database operation failed; playback stopped.",
                persist_failure=False,
            )
            self._events.close()
            raise
        finally:
            self.requests.pending = None
        if reply is not None:
            outcome, replayed = reply
            if isinstance(message, Request):
                _LOGGER.info(
                    "command.finished request=%s code=%s replayed=%s",
                    message.request_id,
                    outcome.code,
                    replayed,
                )
            return CommandReply(outcome, self._published, replayed)
        return self.snapshot

    async def _apply_metadata(self, entry_id: UUID, values: TrackMetadata) -> None:
        await self._send(MetadataResolved(entry_id, values))

    async def _send_radio(self, message: RadioMessage) -> None:
        await self._send(message)

    async def _refresh_metadata(self, event: SessionEvent) -> None:
        if self._metadata is not None:
            self._metadata.wake()

    async def _refill_radio(self, event: SessionEvent) -> None:
        await self._invalidate(RefillRadio(self.radio.status.session_id))

    async def _prepare_playback(self, event: SessionEvent) -> None:
        await self._invalidate(SessionAction.PREPARE)

    async def _invalidate(self, message: RefillRadio | SessionAction) -> None:
        try:
            await self._send(message)
        except RuntimeError:
            if not self.state.closing and not self.state.faulted:
                raise

    async def _control(self, message: commands.Command | Enqueue) -> PlayerSnapshot:
        return cast(PlayerSnapshot, await self._send(message))

    async def enqueue(self, entry: QueueEntry) -> PlayerSnapshot:
        return await self._control(Enqueue(entry))

    async def remove(self, entry_id: UUID) -> PlayerSnapshot:
        return await self._control(commands.Remove(entry_id))

    async def move_before(
        self, entry_id: UUID, before_entry_id: UUID | None = None
    ) -> PlayerSnapshot:
        return await self._control(
            commands.Move(
                entry_id, before_entry_id, self.state.revisions.queue_revision
            )
        )

    async def clear(self) -> PlayerSnapshot:
        return await self._control(commands.Clear(self.state.revisions.queue_revision))

    async def connect(self, channel_id: int) -> PlayerSnapshot:
        return await self._control(commands.Connect(channel_id))

    async def disconnect(self) -> PlayerSnapshot:
        return await self._control(commands.Disconnect())

    async def play(self) -> PlayerSnapshot:
        return await self._control(commands.Control("play", self.playback.attempt_id))

    async def pause(self) -> PlayerSnapshot:
        return await self._control(commands.Control("pause", self.playback.attempt_id))

    async def skip(self) -> PlayerSnapshot:
        return await self._control(commands.Control("skip", self.playback.attempt_id))

    async def stop(self) -> PlayerSnapshot:
        return await self._control(commands.Control("stop", self.playback.attempt_id))

    async def set_volume(self, volume: float) -> PlayerSnapshot:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")

        return await self._control(commands.Volume(volume))

    async def close(self) -> None:
        if self._close_task is None:
            self.state.closing = True
            self._events.close()
            self._space.set()
            self._ready.set()
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    def close_events(self) -> None:
        self._events.close()

    async def _checkpoint_loop(self) -> None:
        while True:
            await asyncio.sleep(CHECKPOINT_INTERVAL_SECONDS)
            try:
                await self._send(SessionAction.CHECKPOINT)
            except (RuntimeError, StorageError):
                return

    async def _publish(self) -> None:
        if self.status == self._published and self.requests.pending is None:
            return
        self.playback.update_position(self._published)
        await self.playback.save_checkpoint()
        changed = self.status != self._published
        self.state.revisions = await self.state.worker.call(
            lambda player: player.publish(
                self._published.revision, changed, self.requests.pending
            )
        )
        status = self.status
        if status != self._published:
            previous = self._published
            self._published = status
            self.radio.observe_playback()
            self._events.publish(status)
            if previous.player.upcoming != status.player.upcoming:
                self.events.publish(QueueChanged(previous, status))
            if (
                previous.player.current != status.player.current
                or previous.player.state != status.player.state
                or previous.player.voice_state != status.player.voice_state
                or previous.player.crossfade_seconds != status.player.crossfade_seconds
                or previous.attempt_id != status.attempt_id
                or previous.channel_id != status.channel_id
                or previous.volume != status.volume
                or previous.last_issue != status.last_issue
            ):
                self.events.publish(PlaybackChanged(previous, status))
            if previous.radio != status.radio:
                self.events.publish(RadioChanged(previous, status))
            previous_plays = {item.id for item in previous.player.recently_played}
            for item in reversed(status.player.recently_played):
                if item.id not in previous_plays:
                    self.events.publish(TrackStarted(previous, status, item))

    async def _close(self) -> None:
        errors: list[Exception] = []
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
            await asyncio.gather(self._checkpoint_task, return_exceptions=True)
        for close in (
            self.events.close,
            self._finish_inbox,
            self.radio.close,
            *((self._metadata.close,) if self._metadata else ()),
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
        try:
            if self.snapshot.state is PlaybackState.PLAYING:
                self.voice.pause()
            await self.playback.save_checkpoint()
        except Exception as exc:
            errors.append(exc)
        for close in (
            self.playback.halt,
            self.voice.close,
            self.state.worker.close,
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Playback shutdown failed.", errors)

    async def _finish_inbox(self) -> None:
        (result,) = await asyncio.gather(self._runner, return_exceptions=True)
        if isinstance(result, asyncio.CancelledError):
            raise RuntimeError(
                "Session inbox was cancelled during shutdown."
            ) from result
        if isinstance(result, Exception):
            raise result


class SessionRequests:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.pending: Receipt | None = None

    async def handle(self, request: Request) -> tuple[Outcome, bool]:
        request_id, command = request.request_id, request.command
        actor_id, actor = request.actor_id, request.actor
        receipt = Receipt(
            request_id,
            commands.fingerprint(command, authenticated=actor_id is not None),
            actor_id=actor_id,
            actor=actor,
        )
        _LOGGER.info(
            "command.received request=%s actor=%s command=%s attempt=%s",
            request_id,
            actor_id,
            command.action
            if isinstance(command, commands.Control)
            else type(command).__name__,
            self._session.playback.attempt_id,
        )
        existing = await self._session.state.worker.call(
            lambda player: player.reserve(receipt)
        )
        if existing is not None:
            replayed = (
                existing.actor_id == actor_id
                and existing.fingerprint == receipt.fingerprint
            )
            outcome = (
                existing.outcome or Outcome("interrupted", 409)
                if replayed
                else Outcome("idempotency_conflict", 409)
            )
            return outcome, replayed
        try:
            outcome = await self.execute(command, receipt)
        except KeyError:
            outcome = Outcome("entry_not_found", 404)
        except UndoUnavailable:
            outcome = Outcome("undo_unavailable", 410)
        except ValueError:
            outcome = Outcome("invalid_action", 409)
        except VoiceError as exc:
            await self._session.playback.voice_failed(str(exc))
            outcome = Outcome("voice_unavailable", 503)
        self.pending = replace(receipt, outcome=outcome)
        return outcome, False

    async def execute(
        self, command: commands.Command | Enqueue, receipt: Receipt | None = None
    ) -> Outcome:
        actor = receipt.actor if receipt else None
        if receipt is not None and isinstance(
            command, (commands.StartRadio, commands.StopRadio, commands.RetryRadio)
        ):
            if command.expected_session_id != self._session.radio.status.session_id:
                return Outcome("radio_conflict", 409)
        if receipt is not None and isinstance(command, (commands.Move, commands.Clear)):
            if (
                command.expected_queue_revision
                != self._session.state.revisions.queue_revision
            ):
                return Outcome("queue_conflict", 409)
        if isinstance(command, (commands.Control, commands.Seek)):
            if command.expected_playback_id != self._session.playback.attempt_id:
                return Outcome("playback_conflict", 409)
        if isinstance(
            command,
            (
                Enqueue,
                commands.Add,
                commands.AddMany,
                commands.Remove,
                commands.Undo,
                commands.Move,
                commands.Clear,
            ),
        ):
            outcome = await self._session.queue.request(command, receipt)
            if isinstance(command, (commands.Remove, commands.Clear)):
                self._session.radio.remember_removals(outcome.entries)
            if isinstance(command, commands.Clear) and command.contributor_id is None:
                self._session.radio.stop(actor)
            return outcome
        match command:
            case commands.StartRadio(preview_id, _):
                if (
                    self._session.radio_catalog is None
                    or receipt is None
                    or actor is None
                ):
                    raise ValueError("Radio is unavailable.")
                try:
                    preview = self._session.radio_catalog.get(
                        preview_id, receipt.actor_id
                    )
                except ValueError:
                    return Outcome("radio_preview_expired", 410)
                self._session.radio.start(preview, actor)
                return Outcome(actor=actor)
            case commands.StopRadio(_):
                self._session.radio.stop(actor)
                return Outcome(actor=actor)
            case commands.RetryRadio(_):
                self._session.radio.retry(actor)
                return Outcome(actor=actor)
            case commands.Control(action, _):
                await {
                    "play": self._session.playback.play,
                    "pause": self._session.playback.pause,
                    "skip": self._session.playback.skip,
                    "stop": self._session.playback.stop,
                }[action]()
                if action == "stop":
                    self._session.radio.set_actor(actor)
            case commands.Volume(volume):
                self._session.playback.set_volume(volume)
            case commands.Crossfade(seconds):
                outcome = Outcome(actor=actor)
                if receipt is None:
                    await self._session.state.change(
                        lambda player: player.set_crossfade(seconds)
                    )
                else:
                    await self._session.state.change(
                        lambda player: player.apply_request(
                            replace(receipt, outcome=outcome),
                            lambda player: player.set_crossfade(seconds),
                        )
                    )
                return outcome
            case commands.Seek(position_seconds, _):
                await self._session.playback.seek(position_seconds)
            case commands.Connect(channel_id):
                await self._session.playback.connect(channel_id)
            case commands.Disconnect():
                await self._session.playback.leave()
                self._session.radio.set_actor(actor)
        return Outcome()


class SessionManager:
    def __init__(self) -> None:
        self._session: Session | None = None
        self._started = False

    async def create(
        self,
        store_factory: Callable[[], PlayerStore],
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        metadata_resolver: MetadataResolver | None = None,
        catalog: MediaCatalog | None = None,
        radio_catalog: RadioCatalog | None = None,
    ) -> Session:
        if self._started:
            raise RuntimeError("The music Session has already been created.")
        self._started = True
        self._session = await Session.create(
            store_factory,
            resolver,
            voice,
            metadata_resolver=metadata_resolver,
            catalog=catalog,
            radio_catalog=radio_catalog,
        )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

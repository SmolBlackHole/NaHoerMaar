# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Serialize player mutations and coordinate cancellable audio side effects."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from math import isfinite
from pathlib import Path
from uuid import UUID, uuid4

from . import commands
from .audio import (
    MetadataResolver,
    ResolvedTrack,
    SourceResolver,
    TrackError,
    VoiceChannelInfo,
    VoiceError,
    VoiceOutput,
)
from .commands import Outcome, Receipt, Revisions
from .events import Snapshots
from .fsm import VoiceEvent
from .models import PlaybackState, PlayerSnapshot, QueueEntry, TrackMetadata
from .player import Player
from .storage import SQLiteStore, StorageError


@dataclass(frozen=True, slots=True)
class PlaybackIssue:
    entry_id: UUID | None
    message: str
    fatal: bool = False


@dataclass(frozen=True, slots=True)
class PlaybackStatus:
    player: PlayerSnapshot
    attempt_id: UUID | None
    channel_id: int | None
    volume: float
    last_issue: PlaybackIssue | None
    revision: int = 0
    queue_revision: int = 0
    position_seconds: float = 0
    position_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommandReply:
    outcome: Outcome
    status: PlaybackStatus
    replayed: bool = False


class _PlayerWorker:
    """Create, use and close the synchronous player on one dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="player")
        self._store: SQLiteStore | None = None
        self._player: Player | None = None

    async def open(self, path: Path) -> PlayerSnapshot:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._open, path
        )

    def _open(self, path: Path) -> PlayerSnapshot:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteStore(path)
        self._player = Player(self._store)
        self._player.recover_requests()
        self._player.publish(self._player.revisions.revision, True)
        return self._player.snapshot

    async def call[T](self, operation: Callable[[Player], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._call, operation
        )

    def _call[T](self, operation: Callable[[Player], T]) -> T:
        if self._player is None:
            raise RuntimeError("Player worker is not open.")
        return operation(self._player)

    async def close(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close
            )
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True)

    def _close(self) -> None:
        self._player = None
        if self._store is not None:
            self._store.close()
            self._store = None


class PlaybackController:
    def __init__(
        self,
        worker: _PlayerWorker,
        snapshot: PlayerSnapshot,
        resolver: SourceResolver,
        voice: VoiceOutput,
        revisions: Revisions,
        metadata_resolver: MetadataResolver | None = None,
    ) -> None:
        self._worker = worker
        self._snapshot = snapshot
        self._resolver = resolver
        self._voice = voice
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._attempt_id: UUID | None = None
        self._retried = False
        self._history_recorded = False
        self._volume = 1.0
        self._last_issue: PlaybackIssue | None = None
        self._revisions = revisions
        self._position_seconds = 0.0
        self._position_origin = 0.0
        self._resolved_track: ResolvedTrack | None = None
        self._position_updated_at: datetime | None = None
        self._position_clock: float | None = None
        self._pending_receipt: Receipt | None = None
        self._events: Snapshots[PlaybackStatus] = Snapshots()
        self._faulted = False
        self._closing = False
        self._load_task: asyncio.Task[None] | None = None
        self._commands: set[asyncio.Task[PlayerSnapshot]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._published = self.status
        self._metadata_wake = asyncio.Event()
        self._metadata_wake.set()
        self._metadata_task = (
            asyncio.create_task(self._enrich_queue(metadata_resolver))
            if metadata_resolver is not None
            else None
        )
        voice.set_disconnect_handler(self._disconnected_callback)

    @classmethod
    async def create(
        cls,
        database: Path,
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        metadata_resolver: MetadataResolver | None = None,
    ) -> PlaybackController:
        worker = _PlayerWorker()
        opening = asyncio.create_task(worker.open(database))
        try:
            snapshot = await asyncio.shield(opening)
            versions = await worker.call(lambda player: player.revisions)
            return cls(worker, snapshot, resolver, voice, versions, metadata_resolver)
        except BaseException:
            await asyncio.shield(worker.close())
            # Retrieve a late initialization error if the caller was cancelled.
            if opening.done() and not opening.cancelled():
                opening.exception()
            raise

    @property
    def snapshot(self) -> PlayerSnapshot:
        return self._snapshot

    @property
    def status(self) -> PlaybackStatus:
        return PlaybackStatus(
            self._snapshot,
            self._attempt_id,
            self._voice.channel_id,
            self._volume,
            self._last_issue,
            self._revisions.revision,
            self._revisions.queue_revision,
            self._position_seconds,
            self._position_updated_at,
        )

    async def read_status(self) -> PlaybackStatus:
        async with self._lock:
            self._require_available()
            return self._published

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[PlaybackStatus | None]]:
        async with self._lock:
            self._require_available()
            queue = self._events.subscribe(self._published)
        try:
            yield queue
        finally:
            self._events.unsubscribe(queue)

    def _require_available(self) -> None:
        if self._closing:
            raise RuntimeError("Playback controller is closed.")
        if self._faulted:
            raise RuntimeError("Playback is halted after an operational failure.")

    def channels(self) -> tuple[VoiceChannelInfo, ...]:
        return self._voice.channels()

    def _submit(
        self, action: Callable[[], Awaitable[None]]
    ) -> asyncio.Task[PlayerSnapshot]:
        task = asyncio.create_task(self._execute(action))
        self._commands.add(task)
        task.add_done_callback(self._command_done)
        return task

    def _command_done(self, task: asyncio.Task[PlayerSnapshot]) -> None:
        self._commands.discard(task)
        if not task.cancelled():
            task.exception()

    async def _execute(self, action: Callable[[], Awaitable[None]]) -> PlayerSnapshot:
        async with self._lock:
            self._require_available()
            self._pending_receipt = None
            try:
                try:
                    await action()
                except VoiceError as exc:
                    await self._voice_failed(str(exc))
                    await self._publish()
                    raise
                except (StorageError, ValueError, KeyError):
                    raise
                except Exception:
                    await self._fault("Playback operation failed; restart the backend.")
                    await self._publish()
                    raise
                await self._publish()
            except StorageError:
                await self._fault(
                    "Player database operation failed; playback stopped.",
                    persist_failure=False,
                )
                self._events.close()
                raise
            finally:
                self._pending_receipt = None
            return self._snapshot

    async def _publish(self) -> None:
        self._update_position()
        changed = self.status != self._published
        self._revisions = await self._worker.call(
            lambda player: player.publish(
                self._published.revision, changed, self._pending_receipt
            )
        )
        status = self.status
        if status != self._published:
            self._published = status
            self._events.publish(status)

    def _update_position(self) -> None:
        previous = self._published
        now = self._loop.time()
        if previous.attempt_id != self._attempt_id:
            self._position_seconds = self._position_origin if self._attempt_id else 0.0
        elif previous.player.state is self._snapshot.state:
            return
        elif (
            previous.player.state is PlaybackState.PLAYING
            and self._position_clock is not None
        ):
            self._position_seconds += now - self._position_clock
        self._position_clock = now
        self._position_updated_at = datetime.now(UTC)

    async def request(
        self, request_id: UUID, command: commands.Command
    ) -> CommandReply:
        receipt = Receipt(request_id, commands.fingerprint(command))
        outcome = Outcome()
        replayed = False

        async def action() -> None:
            nonlocal outcome, replayed
            existing = await self._worker.call(lambda player: player.reserve(receipt))
            if existing is not None:
                replayed = existing.fingerprint == receipt.fingerprint
                outcome = (
                    existing.outcome or Outcome("interrupted", 409)
                    if replayed
                    else Outcome("idempotency_conflict", 409)
                )
                return
            try:
                outcome = await self._dispatch(command, receipt)
            except KeyError:
                outcome = Outcome("entry_not_found", 404)
            except ValueError:
                outcome = Outcome("invalid_action", 409)
            except VoiceError as exc:
                await self._voice_failed(str(exc))
                outcome = Outcome("voice_unavailable", 503)
            self._pending_receipt = replace(receipt, outcome=outcome)

        await asyncio.shield(self._submit(action))
        # A later action may already have committed. Return its fresh snapshot,
        # while retaining the original request's outcome.
        return CommandReply(outcome, await self.read_status(), replayed)

    async def _dispatch(self, command: commands.Command, receipt: Receipt) -> Outcome:
        if isinstance(command, (commands.Move, commands.Clear)):
            if command.expected_queue_revision != self._revisions.queue_revision:
                return Outcome("queue_conflict", 409)
        if isinstance(command, (commands.Control, commands.Seek)):
            if command.expected_playback_id != self._attempt_id:
                return Outcome("playback_conflict", 409)
        operation: Callable[[Player], PlayerSnapshot]
        match command:
            case commands.Add(source_url, added_by):
                entry = QueueEntry(source_url, added_by=added_by)
                operation = partial(Player.enqueue, entry=entry)
                outcome = Outcome(entry_id=entry.id)
            case commands.Remove(entry_id):
                operation = partial(Player.remove, entry_id=entry_id)
                outcome = Outcome()
            case commands.Move(entry_id, before_entry_id, _):
                operation = partial(
                    Player.move_before,
                    entry_id=entry_id,
                    before_entry_id=before_entry_id,
                )
                outcome = Outcome()
            case commands.Clear():
                operation = Player.clear
                outcome = Outcome()
            case commands.Control(action, _):
                await {
                    "play": self._play,
                    "pause": self._pause,
                    "skip": self._skip,
                    "stop": self._stop,
                }[action]()
                return Outcome()
            case commands.Volume(volume):
                self._set_volume(volume)
                return Outcome()
            case commands.Seek(position_seconds, _):
                await self._seek(position_seconds)
                return Outcome()
            case commands.Connect(channel_id):
                await self._connect(channel_id)
                return Outcome()
            case commands.Disconnect():
                await self._leave()
                return Outcome()
        await self._change(
            lambda player: player.apply_request(
                replace(receipt, outcome=outcome), operation
            )
        )
        return outcome

    async def _change(self, operation: Callable[[Player], PlayerSnapshot]) -> None:
        self._snapshot = await self._worker.call(operation)
        self._metadata_wake.set()

    async def _enrich_queue(self, resolver: MetadataResolver) -> None:
        attempted: set[UUID] = set()
        while not self._closing and not self._faulted:
            await self._metadata_wake.wait()
            self._metadata_wake.clear()
            attempted.intersection_update(entry.id for entry in self._snapshot.upcoming)
            entry = next(
                (
                    entry
                    for entry in self._snapshot.upcoming
                    if entry.id not in attempted
                    and (entry.title is None or entry.duration_seconds is None)
                ),
                None,
            )
            if entry is None:
                continue
            attempted.add(entry.id)
            try:
                metadata = await resolver.metadata(entry.source_url)
            except TrackError:
                self._metadata_wake.set()
                continue
            except Exception:
                logging.getLogger(__name__).warning("Could not load queue metadata.")
                self._metadata_wake.set()
                continue

            async def apply_metadata(
                entry_id: UUID = entry.id, values: TrackMetadata = metadata
            ) -> None:
                await self._change(lambda player: player.enrich(entry_id, values))

            try:
                await asyncio.shield(self._submit(apply_metadata))
            except (RuntimeError, StorageError):
                return
            self._metadata_wake.set()

    async def enqueue(self, entry: QueueEntry) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(lambda player: player.enqueue(entry))

        return await asyncio.shield(self._submit(action))

    async def remove(self, entry_id: UUID) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(lambda player: player.remove(entry_id))

        return await asyncio.shield(self._submit(action))

    async def move_before(
        self, entry_id: UUID, before_entry_id: UUID | None = None
    ) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(
                lambda player: player.move_before(entry_id, before_entry_id)
            )

        return await asyncio.shield(self._submit(action))

    async def clear(self) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(Player.clear)

        return await asyncio.shield(self._submit(action))

    async def connect(self, channel_id: int) -> PlayerSnapshot:
        async def action() -> None:
            await self._connect(channel_id)

        return await asyncio.shield(self._submit(action))

    async def _connect(self, channel_id: int) -> None:
        if self._voice.connected and self._voice.channel_id == channel_id:
            return
        await self._leave()
        await self._change(lambda player: player.voice(VoiceEvent.CONNECT))
        await self._voice.connect(channel_id)
        await self._change(lambda player: player.voice(VoiceEvent.CONNECTED))

    async def disconnect(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._leave))

    async def _leave(self) -> None:
        await self._halt()
        await self._change(lambda player: player.voice(VoiceEvent.DISCONNECT))
        await self._voice.disconnect()

    async def play(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._play))

    async def _play(self) -> None:
        if not self._voice.connected:
            raise ValueError("Join a voice channel before starting playback.")
        was_paused = self._snapshot.state is PlaybackState.PAUSED
        await self._change(Player.play)
        self._last_issue = None
        if was_paused:
            self._voice.resume()
        else:
            self._begin()

    async def pause(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._pause))

    async def _pause(self) -> None:
        await self._change(Player.pause)
        self._voice.pause()

    async def skip(self) -> PlayerSnapshot:
        target = self._attempt_id

        async def action() -> None:
            if target != self._attempt_id:
                return
            await self._skip()

        return await asyncio.shield(self._submit(action))

    async def _skip(self) -> None:
        if not self._voice.connected:
            await self._leave()
            return
        await self._change(Player.skip)
        await self._halt()
        self._begin()

    async def stop(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._stop))

    async def _stop(self) -> None:
        await self._change(Player.stop)
        await self._halt()

    async def set_volume(self, volume: float) -> PlayerSnapshot:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")

        async def action() -> None:
            self._set_volume(volume)

        return await asyncio.shield(self._submit(action))

    def _set_volume(self, volume: float) -> None:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")
        if volume != self._volume:
            self._voice.set_volume(volume)
            self._volume = volume

    async def _seek(self, position_seconds: float) -> None:
        entry = self._snapshot.current
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
        await self._change(Player.seek)
        was_paused = self._snapshot.state is PlaybackState.PAUSED
        await self._halt()
        attempt_id = uuid4()
        self._attempt_id = attempt_id
        self._position_origin = position_seconds
        try:
            self._voice.play(
                track,
                lambda error: self._completed_callback(attempt_id, error),
                position_seconds=position_seconds,
            )
        except TrackError as exc:
            await self._track_failed(exc)
            return
        self._resolved_track = track
        if was_paused:
            self._voice.pause()

    def _begin(self, *, retried: bool = False) -> None:
        entry = self._snapshot.current
        if entry is None:
            return
        attempt_id = uuid4()
        self._attempt_id = attempt_id
        self._position_origin = 0.0
        self._retried = retried
        if not retried:
            self._history_recorded = False
        self._load_task = asyncio.create_task(self._load(attempt_id, entry))

    async def _load(self, attempt_id: UUID, entry: QueueEntry) -> None:
        try:
            track = await self._resolver.resolve(entry.source_url)
        except asyncio.CancelledError:
            raise
        except TrackError as exc:

            async def failed(error: TrackError = exc) -> None:
                if self._attempt_id == attempt_id:
                    await self._track_failed(error)

            self._submit(failed)
            return
        except Exception as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise VoiceError("Source extraction cleanup failed.") from exc

            async def broken() -> None:
                if self._attempt_id == attempt_id:
                    await self._fault("Source resolver failed; restart the backend.")

            self._submit(broken)
            return

        async def ready() -> None:
            if self._attempt_id != attempt_id:
                return
            if not self._voice.connected:
                await self._leave()
                return
            await self._change(lambda player: player.enrich(entry.id, track.metadata))
            try:
                self._voice.play(
                    track,
                    lambda error: self._completed_callback(attempt_id, error),
                )
            except TrackError as exc:
                await self._track_failed(exc)
                return
            await self._change(
                lambda player: player.mark_playing(
                    record_history=not self._history_recorded
                )
            )
            self._history_recorded = True
            self._resolved_track = track

        self._submit(ready)

    def _completed_callback(self, attempt_id: UUID, error: Exception | None) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue_completion, attempt_id, error)

    def _queue_completion(self, attempt_id: UUID, error: Exception | None) -> None:
        if self._closing:
            return

        async def action() -> None:
            if self._attempt_id != attempt_id:
                return
            if not self._voice.connected:
                await self._leave()
                return
            if error is not None:
                if isinstance(error, TrackError):
                    await self._track_failed(error)
                else:
                    raise VoiceError("Audio output failed.") from error
            else:
                await self._change(Player.finished)
                await self._halt()
                self._begin()

        self._submit(action)

    async def _track_failed(self, error: TrackError) -> None:
        if not self._voice.connected:
            await self._leave()
            return
        entry = self._snapshot.current
        if entry is None:
            return
        retry = error.retryable and not self._retried
        self._last_issue = PlaybackIssue(entry.id, str(error))
        await self._change(Player.fail)
        await self._halt()
        await self._change(Player.play if retry else Player.skip)
        self._begin(retried=retry)

    async def _halt(self) -> None:
        self._attempt_id = None
        self._resolved_track = None
        task, self._load_task = self._load_task, None
        try:
            if task is not None:
                if not task.done():
                    task.cancel()
                results = await asyncio.gather(task, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result
        finally:
            await self._voice.stop()

    def _disconnected_callback(self) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue_disconnect)

    def _queue_disconnect(self) -> None:
        if self._closing:
            return

        async def action() -> None:
            if not self._voice.connected:
                self._last_issue = PlaybackIssue(None, "Discord voice connection lost.")
                await self._leave()

        self._submit(action)

    async def _fault(self, message: str, *, persist_failure: bool = True) -> None:
        self._faulted = True
        entry = self._snapshot.current
        if persist_failure and self._snapshot.state in (
            PlaybackState.LOADING,
            PlaybackState.PLAYING,
            PlaybackState.PAUSED,
        ):
            try:
                await self._change(Player.fail)
            except StorageError:
                message = f"{message} The error state could not be saved."
        self._last_issue = PlaybackIssue(entry.id if entry else None, message, True)
        try:
            await self._halt()
        except Exception:
            self._last_issue = PlaybackIssue(
                entry.id if entry else None,
                f"{message} Audio cleanup also failed.",
                True,
            )

    async def _voice_failed(self, message: str) -> None:
        self._last_issue = PlaybackIssue(None, message)
        try:
            await self._leave()
        except Exception as exc:
            await self._fault(
                f"{message} Could not restore the stopped player.",
                persist_failure=not isinstance(exc, StorageError),
            )

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._events.close()
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    def close_events(self) -> None:
        self._events.close()

    async def _close(self) -> None:
        errors: list[Exception] = []
        if self._metadata_task is not None:
            self._metadata_task.cancel()
            await asyncio.gather(self._metadata_task, return_exceptions=True)
        async with self._lock:
            try:
                await self._halt()
            except Exception as exc:
                errors.append(exc)
            try:
                if not self._faulted:
                    await self._change(
                        lambda player: player.voice(VoiceEvent.DISCONNECT)
                    )
            except Exception as exc:
                errors.append(exc)
            try:
                await self._voice.disconnect()
            except Exception as exc:
                errors.append(exc)
            try:
                await self._worker.close()
            except Exception as exc:
                errors.append(exc)
        if self._commands:
            await asyncio.gather(*tuple(self._commands), return_exceptions=True)
        if errors:
            raise ExceptionGroup("Playback shutdown failed.", errors)

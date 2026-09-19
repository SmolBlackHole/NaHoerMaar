# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Serialize player mutations and coordinate cancellable audio side effects."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from uuid import UUID, uuid4

from .audio import SourceResolver, TrackError, VoiceChannelInfo, VoiceError, VoiceOutput
from .fsm import VoiceEvent
from .models import PlaybackState, PlayerSnapshot, QueueEntry
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
        return self._player.snapshot

    async def call(
        self, operation: Callable[[Player], PlayerSnapshot]
    ) -> PlayerSnapshot:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._call, operation
        )

    def _call(self, operation: Callable[[Player], PlayerSnapshot]) -> PlayerSnapshot:
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
    ) -> None:
        self._worker = worker
        self._snapshot = snapshot
        self._resolver = resolver
        self._voice = voice
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._attempt_id: UUID | None = None
        self._retried = False
        self._volume = 1.0
        self._last_issue: PlaybackIssue | None = None
        self._faulted = False
        self._closing = False
        self._load_task: asyncio.Task[None] | None = None
        self._commands: set[asyncio.Task[PlayerSnapshot]] = set()
        self._close_task: asyncio.Task[None] | None = None
        voice.set_disconnect_handler(self._disconnected_callback)

    @classmethod
    async def create(
        cls, database: Path, resolver: SourceResolver, voice: VoiceOutput
    ) -> PlaybackController:
        worker = _PlayerWorker()
        opening = asyncio.create_task(worker.open(database))
        try:
            snapshot = await asyncio.shield(opening)
            return cls(worker, snapshot, resolver, voice)
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
        )

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
            if self._closing:
                raise RuntimeError("Playback controller is closed.")
            if self._faulted:
                raise RuntimeError("Playback is halted after an operational failure.")
            try:
                await action()
            except StorageError:
                await self._fault(
                    "Player database operation failed; playback stopped.",
                    persist_failure=False,
                )
                raise
            except VoiceError as exc:
                await self._voice_failed(str(exc))
                raise
            except (ValueError, KeyError):
                raise
            except Exception:
                await self._fault("Playback operation failed; restart the backend.")
                raise
            return self._snapshot

    async def _change(self, operation: Callable[[Player], PlayerSnapshot]) -> None:
        self._snapshot = await self._worker.call(operation)

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
            if self._voice.connected and self._voice.channel_id == channel_id:
                return
            await self._leave()
            await self._change(lambda player: player.voice(VoiceEvent.CONNECT))
            await self._voice.connect(channel_id)
            await self._change(lambda player: player.voice(VoiceEvent.CONNECTED))

        return await asyncio.shield(self._submit(action))

    async def disconnect(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._leave))

    async def _leave(self) -> None:
        await self._halt()
        await self._change(lambda player: player.voice(VoiceEvent.DISCONNECT))
        await self._voice.disconnect()

    async def play(self) -> PlayerSnapshot:
        async def action() -> None:
            if not self._voice.connected:
                raise ValueError("Join a voice channel before starting playback.")
            was_paused = self._snapshot.state is PlaybackState.PAUSED
            await self._change(Player.play)
            self._last_issue = None
            if was_paused:
                self._voice.resume()
            else:
                self._begin()

        return await asyncio.shield(self._submit(action))

    async def pause(self) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(Player.pause)
            self._voice.pause()

        return await asyncio.shield(self._submit(action))

    async def skip(self) -> PlayerSnapshot:
        target = self._attempt_id

        async def action() -> None:
            if target != self._attempt_id:
                return
            if not self._voice.connected:
                await self._leave()
                return
            await self._change(Player.skip)
            await self._halt()
            self._begin()

        return await asyncio.shield(self._submit(action))

    async def stop(self) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(Player.stop)
            await self._halt()

        return await asyncio.shield(self._submit(action))

    async def set_volume(self, volume: float) -> PlayerSnapshot:
        if not isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Volume must be between 0 and 1.")

        async def action() -> None:
            self._voice.set_volume(volume)
            self._volume = volume

        return await asyncio.shield(self._submit(action))

    def _begin(self, *, retried: bool = False) -> None:
        entry = self._snapshot.current
        if entry is None:
            return
        attempt_id = uuid4()
        self._attempt_id = attempt_id
        self._retried = retried
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
            try:
                self._voice.play(
                    track,
                    lambda error: self._completed_callback(attempt_id, error),
                )
            except TrackError as exc:
                await self._track_failed(exc)
                return
            await self._change(Player.mark_playing)

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
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        errors: list[Exception] = []
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

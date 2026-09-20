# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Serialize player mutations and coordinate cancellable audio side effects."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime
from functools import partial
from math import isfinite
from pathlib import Path
from uuid import UUID, uuid4

from ..domain import commands
from ..domain.commands import Outcome, Receipt, Revisions
from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.fsm import VoiceEvent
from ..domain.models import (
    Contributor,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from ..domain.undo import Removal, UndoUnavailable
from ..domain.radio import RadioEvent, RadioState, RadioStatus, radio_transition
from ..domain.catalog import CatalogTrack
from ..integrations.youtube import video_id
from ..persistence.player_store import StorageError
from .audio import (
    MetadataResolver,
    ResolvedTrack,
    SourceResolver,
    TrackError,
    VoiceChannelInfo,
    VoiceError,
    VoiceOutput,
)
from .catalog import PLAYLIST_LIMIT, MediaCatalog
from .crossfade import CrossfadePreparation, Preparation
from .events import Snapshots
from .metadata import QueueMetadata
from .player import Player
from .radio import RADIO_BUFFER, RADIO_POOL_LIMIT, RadioCatalog
from .status import CommandReply, PlaybackIssue, PlaybackStatus
from .worker import PlayerWorker

CHECKPOINT_INTERVAL_SECONDS = 5.0
_LOGGER = logging.getLogger(__name__)


class PlaybackController:
    def __init__(
        self,
        worker: PlayerWorker,
        snapshot: PlayerSnapshot,
        resolver: SourceResolver,
        voice: VoiceOutput,
        revisions: Revisions,
        metadata_resolver: MetadataResolver | None = None,
        catalog: MediaCatalog | None = None,
        radio_catalog: RadioCatalog | None = None,
        checkpoint: PlaybackCheckpoint | None = None,
    ) -> None:
        self.catalog = catalog
        self.radio_catalog = radio_catalog
        self._radio = RadioStatus()
        self._radio_pool: tuple[CatalogTrack, ...] = ()
        self._radio_removed: set[str] = set()
        self._radio_task: asyncio.Task[None] | None = None
        self._radio_tasks: set[asyncio.Task[None]] = set()
        self._worker = worker
        self._snapshot = snapshot
        self._resolver = resolver
        self._voice = voice
        self._crossfade = CrossfadePreparation(resolver, voice)
        self._deferred_fade: tuple[Preparation, ResolvedTrack] | None = None
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._attempt_id: UUID | None = None
        self._retried = False
        self._history_recorded = False
        self._load_paused = False
        self._restore_checkpoint = checkpoint
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
        self._metadata = (
            QueueMetadata(
                metadata_resolver,
                snapshot=lambda: self._snapshot,
                active=lambda: not self._closing and not self._faulted,
                needs_refresh=lambda source: (
                    self.catalog is not None and self.catalog.needs_refresh(source)
                ),
                apply=self._apply_metadata,
            )
            if metadata_resolver is not None
            else None
        )
        voice.set_disconnect_handler(self._disconnected_callback)
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())

    @classmethod
    async def create(
        cls,
        database: Path,
        resolver: SourceResolver,
        voice: VoiceOutput,
        *,
        metadata_resolver: MetadataResolver | None = None,
        catalog: MediaCatalog | None = None,
        radio_catalog: RadioCatalog | None = None,
    ) -> PlaybackController:
        worker = PlayerWorker()
        opening = asyncio.create_task(worker.open(database))
        try:
            snapshot = await asyncio.shield(opening)
            versions = await worker.call(lambda player: player.revisions)
            checkpoint = await worker.call(lambda player: player.checkpoint)
            return cls(
                worker,
                snapshot,
                resolver,
                voice,
                versions,
                metadata_resolver,
                catalog,
                radio_catalog,
                checkpoint,
            )
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
            self._radio,
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
        await self._save_checkpoint()
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
        self._schedule_radio()
        await self._prepare_crossfade()

    async def restore(self) -> None:
        """Called once the Discord gateway is ready, before serving controls."""

        async def action() -> None:
            checkpoint = self._restore_checkpoint
            if checkpoint is None:
                return
            _LOGGER.info(
                "playback.restoring channel=%s entry=%s position=%.3f paused=%s",
                checkpoint.channel_id,
                checkpoint.entry_id,
                checkpoint.position_seconds,
                checkpoint.paused,
            )
            self._set_volume(checkpoint.volume)
            await self._change(lambda player: player.voice(VoiceEvent.CONNECT))
            await self._voice.connect(checkpoint.channel_id)
            await self._change(lambda player: player.voice(VoiceEvent.CONNECTED))
            self._restore_checkpoint = None
            if checkpoint.entry_id is not None:
                self._begin(
                    position_seconds=checkpoint.position_seconds,
                    paused=checkpoint.paused,
                    record_history=not any(
                        item.entry.id == checkpoint.entry_id
                        for item in self._snapshot.recently_played
                    ),
                )

        try:
            await asyncio.shield(self._submit(action))
        except VoiceError:
            # A missing channel or revoked permission must not take down the API.
            # _execute already reports the failure and keeps the track queued.
            pass

    async def _save_checkpoint(self) -> None:
        if self._restore_checkpoint is not None:
            return
        checkpoint = None
        channel_id = self._voice.channel_id
        if self._voice.connected and channel_id is not None and not self._faulted:
            entry = self._snapshot.current
            position = 0.0
            if entry is not None:
                audio_position = self._voice.position_seconds
                if audio_position is not None:
                    position = audio_position
                else:
                    position = self._position_seconds
                    if (
                        self._snapshot.state is PlaybackState.PLAYING
                        and self._position_clock is not None
                    ):
                        position += self._loop.time() - self._position_clock
            checkpoint = PlaybackCheckpoint(
                channel_id,
                entry.id if entry else None,
                position,
                self._snapshot.state is PlaybackState.PAUSED or self._load_paused,
                self._volume,
            )
        await self._worker.call(lambda player: player.save_checkpoint(checkpoint))

    async def _forget_checkpoint(self) -> None:
        self._restore_checkpoint = None
        await self._worker.call(lambda player: player.save_checkpoint(None))

    async def _checkpoint_loop(self) -> None:
        while True:
            await asyncio.sleep(CHECKPOINT_INTERVAL_SECONDS)
            async with self._lock:
                if self._closing or self._faulted:
                    return
                try:
                    await self._save_checkpoint()
                except StorageError:
                    await self._fault(
                        "Playback position could not be saved; playback stopped.",
                        persist_failure=False,
                    )
                    self._events.close()
                    return

    async def _prepare_crossfade(self) -> None:
        snapshot = self._snapshot
        track = self._resolved_track
        key = None
        if (
            not self._closing
            and not self._faulted
            and snapshot.state in (PlaybackState.PLAYING, PlaybackState.PAUSED)
            and snapshot.crossfade_seconds
            and snapshot.upcoming
            and self._attempt_id is not None
            and track is not None
            and track.duration_seconds is not None
            and track.duration_seconds > 0
            and not self._voice.transitioning
        ):
            entry = snapshot.upcoming[0]
            key = Preparation(
                self._attempt_id,
                entry.id,
                snapshot.crossfade_seconds,
                entry.source_url,
                track.duration_seconds,
            )
        await self._crossfade.sync(key, self._crossfade_due, self._preparation_failed)
        if self._deferred_fade is not None and snapshot.state is PlaybackState.PLAYING:
            deferred, self._deferred_fade = self._deferred_fade, None
            self._crossfade_due(*deferred)

    def _preparation_failed(self, key: Preparation, error: VoiceError) -> None:
        if self._closing or self._faulted:
            return

        async def action() -> None:
            if key == self._crossfade.key:
                raise error

        self._submit(action)

    def _crossfade_due(self, key: Preparation, track: ResolvedTrack) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue_crossfade, key, track)

    def _queue_crossfade(self, key: Preparation, track: ResolvedTrack) -> None:
        if self._closing or self._faulted:
            return

        async def action() -> None:
            if key != self._crossfade.key or key.attempt_id != self._attempt_id:
                return
            if self._snapshot.state is PlaybackState.PAUSED:
                self._deferred_fade = (key, track)
                return
            if not self._voice.connected:
                await self._leave()
                return
            _LOGGER.info(
                "crossfade.start outgoing_attempt=%s incoming_entry=%s seconds=%s "
                "position=%s",
                key.attempt_id,
                key.entry_id,
                key.seconds,
                self._voice.position_seconds,
            )
            await self._change(
                lambda player: player.crossfade(key.entry_id, track.metadata)
            )
            attempt_id = uuid4()
            self._attempt_id = attempt_id
            self._position_origin = 0.0
            self._retried = False
            self._history_recorded = True
            self._resolved_track = track

            def callback(error: Exception | None) -> None:
                self._completed_callback(attempt_id, error)

            if not self._voice.start_transition(track, callback, self._fade_finished):
                _LOGGER.warning(
                    "crossfade.fallback attempt=%s entry=%s reason=activation_unavailable",
                    attempt_id,
                    key.entry_id,
                )
                # EOF may win while the database commits. The new current entry
                # still starts once, without a second history record.
                await self._voice.stop()
                try:
                    self._voice.play(track, callback)
                except TrackError as error:
                    await self._track_failed(error)

        self._submit(action)

    def _fade_finished(self) -> None:
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue_fade_finished)

    def _queue_fade_finished(self) -> None:
        if self._closing or self._faulted:
            return

        async def action() -> None:
            _LOGGER.info("crossfade.finished attempt=%s", self._attempt_id)

        self._submit(action)

    def _update_position(self) -> None:
        previous = self._published
        now = self._loop.time()
        if previous.attempt_id != self._attempt_id:
            self._position_seconds = self._position_origin if self._attempt_id else 0.0
        elif previous.player.state is self._snapshot.state:
            return
        elif previous.player.state is PlaybackState.LOADING:
            self._position_seconds = self._position_origin
        elif (
            previous.player.state is PlaybackState.PLAYING
            and self._position_clock is not None
        ):
            self._position_seconds += now - self._position_clock
        self._position_clock = now
        self._position_updated_at = datetime.now(UTC)

    async def request(
        self,
        request_id: UUID,
        command: commands.Command,
        *,
        actor_id: UUID | None = None,
        actor: Contributor | None = None,
    ) -> CommandReply:
        receipt = Receipt(
            request_id,
            commands.fingerprint(command, authenticated=actor_id is not None),
            actor_id=actor_id,
            actor=actor,
        )
        outcome = Outcome()
        replayed = False

        async def action() -> None:
            nonlocal outcome, replayed
            _LOGGER.info(
                "command.received request=%s actor=%s command=%s attempt=%s",
                request_id,
                actor_id,
                command.action
                if isinstance(command, commands.Control)
                else type(command).__name__,
                self._attempt_id,
            )
            existing = await self._worker.call(lambda player: player.reserve(receipt))
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
                return
            try:
                outcome = await self._dispatch(command, receipt)
            except KeyError:
                outcome = Outcome("entry_not_found", 404)
            except UndoUnavailable:
                outcome = Outcome("undo_unavailable", 410)
            except ValueError:
                outcome = Outcome("invalid_action", 409)
            except VoiceError as exc:
                await self._voice_failed(str(exc))
                outcome = Outcome("voice_unavailable", 503)
            self._pending_receipt = replace(receipt, outcome=outcome)

        await asyncio.shield(self._submit(action))
        _LOGGER.info(
            "command.finished request=%s code=%s replayed=%s",
            request_id,
            outcome.code,
            replayed,
        )
        # A later action may already have committed. Return its fresh snapshot,
        # while retaining the original request's outcome.
        return CommandReply(outcome, await self.read_status(), replayed)

    async def _dispatch(self, command: commands.Command, receipt: Receipt) -> Outcome:
        if isinstance(
            command, (commands.StartRadio, commands.StopRadio, commands.RetryRadio)
        ):
            if command.expected_session_id != self._radio.session_id:
                return Outcome("radio_conflict", 409)
        if isinstance(command, (commands.Move, commands.Clear)):
            if command.expected_queue_revision != self._revisions.queue_revision:
                return Outcome("queue_conflict", 409)
        if isinstance(command, (commands.Control, commands.Seek)):
            if command.expected_playback_id != self._attempt_id:
                return Outcome("playback_conflict", 409)
        operation: Callable[[Player], PlayerSnapshot]
        match command:
            case commands.StartRadio(preview_id, _):
                if self.radio_catalog is None or receipt.actor is None:
                    raise ValueError("Radio is unavailable.")
                try:
                    preview = self.radio_catalog.get(preview_id, receipt.actor_id)
                except ValueError:
                    return Outcome("radio_preview_expired", 410)
                self._stop_radio()
                self._radio = RadioStatus(
                    radio_transition(self._radio.state, RadioEvent.START),
                    uuid4(),
                    preview.seed,
                    receipt.actor,
                    event_id=uuid4(),
                    action="started",
                    actor=receipt.actor,
                )
                self._radio_pool = preview.entries
                self._radio_removed.clear()
                return Outcome(actor=receipt.actor)
            case commands.StopRadio(_):
                self._stop_radio(receipt.actor)
                return Outcome(actor=receipt.actor)
            case commands.RetryRadio(_):
                self._radio = replace(
                    self._radio,
                    state=radio_transition(self._radio.state, RadioEvent.RETRY),
                    error=None,
                    event_id=uuid4(),
                    action="retried",
                    actor=receipt.actor,
                )
                return Outcome(actor=receipt.actor)
            case commands.Add(source_url, added_by):
                entry = self._queue_entry(source_url, added_by)
                operation = partial(Player.enqueue, entry=entry)
                outcome = Outcome(entry_id=entry.id, added_count=1, entries=(entry,))
            case commands.AddMany(source_urls, added_by, skip_duplicates):
                if not 1 <= len(source_urls) <= PLAYLIST_LIMIT:
                    raise ValueError("Invalid batch size.")
                selected: list[str] = list(source_urls)
                if skip_duplicates:
                    active = self._snapshot.upcoming + (
                        (self._snapshot.current,) if self._snapshot.current else ()
                    )
                    seen = {
                        video_id(entry.source_url) or entry.source_url
                        for entry in active
                    }
                    selected = []
                    for url in source_urls:
                        identifier = video_id(url) or url
                        if identifier not in seen:
                            selected.append(url)
                            seen.add(identifier)
                entries = tuple(self._queue_entry(url, added_by) for url in selected)
                operation = partial(Player.enqueue_many, entries=entries)
                outcome = Outcome(
                    added_count=len(entries),
                    skipped_count=len(source_urls) - len(entries),
                    entries=entries,
                )
            case commands.Remove(entry_id):
                operation = partial(Player.remove, entry_id=entry_id)
                outcome = Outcome()
            case commands.Undo(undo_id):
                removal = await self._worker.call(
                    lambda player: player.removal(undo_id, receipt.actor_id)
                )
                receipt = replace(receipt, consume_undo=undo_id)
                operation = partial(Player.restore_removal, removal=removal)
                outcome = Outcome(
                    restored_count=removal.count,
                    entries=tuple(
                        entry for group in removal.groups for entry in group.entries
                    ),
                )
            case commands.Move(entry_id, before_entry_id, _):
                operation = partial(
                    Player.move_before,
                    entry_id=entry_id,
                    before_entry_id=before_entry_id,
                )
                outcome = Outcome()
            case commands.Clear(_, contributor_id):
                operation = partial(Player.clear, contributor_id=contributor_id)
                outcome = Outcome()
            case commands.Control(action, _):
                await {
                    "play": self._play,
                    "pause": self._pause,
                    "skip": self._skip,
                    "stop": self._stop,
                }[action]()
                if action == "stop":
                    self._radio = replace(self._radio, actor=receipt.actor)
                return Outcome()
            case commands.Volume(volume):
                self._set_volume(volume)
                return Outcome()
            case commands.Crossfade(seconds):
                operation = partial(Player.set_crossfade, seconds=seconds)
                outcome = Outcome()
            case commands.Seek(position_seconds, _):
                await self._seek(position_seconds)
                return Outcome()
            case commands.Connect(channel_id):
                await self._connect(channel_id)
                return Outcome()
            case commands.Disconnect():
                await self._leave()
                self._radio = replace(self._radio, actor=receipt.actor)
                return Outcome()
        if isinstance(command, (commands.Remove, commands.Clear)):
            removed = {
                entry.id
                for entry in self._snapshot.upcoming
                if (
                    isinstance(command, commands.Remove)
                    and entry.id == command.entry_id
                )
                or (
                    isinstance(command, commands.Clear)
                    and (
                        command.contributor_id is None
                        or (
                            entry.added_by is not None
                            and entry.added_by.id == command.contributor_id
                        )
                    )
                )
            }
            outcome = replace(
                outcome,
                removed_count=len(removed),
                entries=tuple(
                    entry for entry in self._snapshot.upcoming if entry.id in removed
                ),
            )
            if removed and receipt.actor_id is not None:
                removal = Removal.capture(
                    self._snapshot.upcoming, removed, receipt.actor_id
                )
                receipt = replace(receipt, removal=removal)
                outcome = replace(
                    outcome, undo_id=removal.id, undo_expires_at=removal.expires_at
                )
        outcome = replace(outcome, actor=receipt.actor)
        await self._change(
            lambda player: player.apply_request(
                replace(receipt, outcome=outcome), operation
            )
        )
        if isinstance(command, (commands.Remove, commands.Clear)):
            self._remember_radio_removals(outcome.entries)
        if isinstance(command, commands.Clear) and command.contributor_id is None:
            self._stop_radio(receipt.actor)
        return outcome

    def _stop_radio(self, actor: Contributor | None = None) -> None:
        if self._radio.state is not RadioState.OFF:
            self._radio = replace(
                self._radio,
                state=radio_transition(self._radio.state, RadioEvent.STOP),
                session_id=None,
                error=None,
                event_id=uuid4(),
                action="stopped",
                actor=actor,
            )
        self._radio_pool = ()
        task, self._radio_task = self._radio_task, None
        if task is not None:
            task.cancel()

    def _remember_radio_removals(self, entries: tuple[QueueEntry, ...]) -> None:
        if self._radio.state is not RadioState.OFF:
            self._radio_removed.update(
                entry.video_id or video_id(entry.source_url) or entry.source_url
                for entry in entries
            )

    def _schedule_radio(self) -> None:
        if (
            self._closing
            or self._faulted
            or self._radio_task is not None
            or self._radio.state is not RadioState.ACTIVE
            or self._snapshot.state is PlaybackState.PAUSED
            or len(self._snapshot.upcoming) >= RADIO_BUFFER
        ):
            return
        task = asyncio.create_task(self._fill_radio())
        self._radio_task = task
        self._radio_tasks.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self._radio_tasks.discard(completed)
            if self._radio_task is completed:
                self._radio_task = None
            if not completed.cancelled():
                completed.exception()
            self._schedule_radio()

        task.add_done_callback(done)

    async def _radio_enqueue(self) -> int:
        active = self._snapshot.upcoming + (
            (self._snapshot.current,) if self._snapshot.current else ()
        )
        excluded = self._radio_removed | {
            entry.video_id or video_id(entry.source_url) or entry.source_url
            for entry in (
                *active,
                *(item.entry for item in self._snapshot.recently_played),
            )
        }
        entries: list[QueueEntry] = []
        remaining: list[CatalogTrack] = []
        for track in self._radio_pool:
            if (
                not track.video_id
                or not track.source_url
                or track.unavailable
                or track.video_id in excluded
            ):
                continue
            excluded.add(track.video_id)
            if len(self._snapshot.upcoming) + len(entries) < RADIO_BUFFER:
                entries.append(
                    QueueEntry(
                        track.source_url,
                        **{
                            field.name: getattr(track, field.name)
                            for field in fields(TrackMetadata)
                        },
                        added_by=self._radio.initiator,
                        origin="radio",
                    )
                )
            else:
                remaining.append(track)
        if entries:
            await self._change(lambda player: player.enqueue_many(tuple(entries)))
        self._radio_pool = tuple(remaining)
        return len(entries)

    async def _fill_radio(self) -> None:
        session = self._radio.session_id
        seed = self._radio.seed
        fetch = False
        continuing = self._snapshot.state in (
            PlaybackState.PLAYING,
            PlaybackState.LOADING,
        )

        async def prepare() -> None:
            nonlocal fetch
            if (
                session != self._radio.session_id
                or self._radio.state is not RadioState.ACTIVE
                or self._snapshot.state is PlaybackState.PAUSED
            ):
                return
            await self._radio_enqueue()
            if len(self._snapshot.upcoming) < RADIO_BUFFER:
                self._radio = replace(
                    self._radio,
                    state=radio_transition(self._radio.state, RadioEvent.FETCH),
                )
                fetch = True

        await asyncio.shield(self._submit(prepare))
        if not fetch or seed is None or self.radio_catalog is None:
            return
        error: str | None = None
        tracks: tuple[CatalogTrack, ...] = ()
        try:
            async with asyncio.timeout(35):
                tracks = await self.radio_catalog.provider.recommend(
                    seed, RADIO_POOL_LIMIT
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            error = "Radio could not load more tracks. Try again."

        async def finish() -> None:
            if (
                session != self._radio.session_id
                or self._radio.state is not RadioState.LOADING
            ):
                return
            if error:
                self._radio = replace(
                    self._radio,
                    state=radio_transition(self._radio.state, RadioEvent.FAIL),
                    error=error,
                )
                return
            self._radio_pool = tracks[:RADIO_POOL_LIMIT]
            if self._snapshot.state is PlaybackState.PAUSED:
                self._radio = replace(
                    self._radio,
                    state=radio_transition(self._radio.state, RadioEvent.READY),
                )
                return
            added = await self._radio_enqueue()
            exhausted = not added and len(self._snapshot.upcoming) < RADIO_BUFFER
            self._radio = replace(
                self._radio,
                state=radio_transition(
                    self._radio.state,
                    RadioEvent.FAIL if exhausted else RadioEvent.READY,
                ),
                error="No new recommendations available. Try again later."
                if exhausted
                else None,
            )
            if (
                continuing
                and self._snapshot.current is None
                and self._snapshot.upcoming
                and self._voice.connected
            ):
                await self._play()

        await asyncio.shield(self._submit(finish))

    def _queue_entry(self, source_url: str, added_by: Contributor | None) -> QueueEntry:
        entry = (
            self.catalog.queue_entry(source_url, added_by)
            if self.catalog
            else QueueEntry(source_url, added_by=added_by)
        )
        identifier = video_id(source_url)
        if identifier and self.radio_catalog:
            metadata = self.radio_catalog.metadata(identifier)
            if metadata:
                entry = replace(
                    entry,
                    **{
                        field.name: getattr(metadata, field.name)
                        for field in fields(TrackMetadata)
                        if getattr(metadata, field.name) is not None
                    },
                )
        known = (
            ((self._snapshot.current,) if self._snapshot.current else ())
            + self._snapshot.upcoming
            + tuple(item.entry for item in self._snapshot.recently_played)
        )
        for candidate in known:
            if (
                identifier
                and (candidate.video_id or video_id(candidate.source_url)) == identifier
            ):
                entry = replace(
                    entry,
                    **{
                        field.name: getattr(candidate, field.name)
                        for field in fields(TrackMetadata)
                        if getattr(entry, field.name) is None
                    },
                )
                if entry.title and entry.duration_seconds is not None:
                    break
        return entry

    async def _change(self, operation: Callable[[Player], PlayerSnapshot]) -> None:
        self._snapshot = await self._worker.call(operation)
        if self._metadata is not None:
            self._metadata.wake()

    async def _apply_metadata(self, entry_id: UUID, values: TrackMetadata) -> None:
        async def action() -> None:
            await self._change(lambda player: player.enrich(entry_id, values))

        await asyncio.shield(self._submit(action))

    async def enqueue(self, entry: QueueEntry) -> PlayerSnapshot:
        async def action() -> None:
            await self._change(lambda player: player.enqueue(entry))

        return await asyncio.shield(self._submit(action))

    async def remove(self, entry_id: UUID) -> PlayerSnapshot:
        async def action() -> None:
            removed = tuple(
                entry for entry in self._snapshot.upcoming if entry.id == entry_id
            )
            await self._change(lambda player: player.remove(entry_id))
            self._remember_radio_removals(removed)

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
            self._stop_radio()

        return await asyncio.shield(self._submit(action))

    async def connect(self, channel_id: int) -> PlayerSnapshot:
        async def action() -> None:
            await self._connect(channel_id)

        return await asyncio.shield(self._submit(action))

    async def _connect(self, channel_id: int) -> None:
        _LOGGER.info(
            "voice.connect requested=%s previous=%s", channel_id, self._voice.channel_id
        )
        if self._voice.connected and self._voice.channel_id == channel_id:
            return
        await self._forget_checkpoint()
        await self._halt()
        await self._change(lambda player: player.voice(VoiceEvent.DISCONNECT))
        await self._voice.disconnect()
        await self._change(lambda player: player.voice(VoiceEvent.CONNECT))
        await self._voice.connect(channel_id)
        await self._change(lambda player: player.voice(VoiceEvent.CONNECTED))

    async def disconnect(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._leave))

    async def _leave(self) -> None:
        _LOGGER.info("voice.leave channel=%s", self._voice.channel_id)
        await self._forget_checkpoint()
        self._stop_radio()
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
        _LOGGER.info(
            "playback.skip attempt=%s position=%s",
            self._attempt_id,
            self._voice.position_seconds,
        )
        if not self._voice.connected:
            await self._leave()
            return
        await self._change(Player.skip)
        await self._halt()
        self._begin()

    async def stop(self) -> PlayerSnapshot:
        return await asyncio.shield(self._submit(self._stop))

    async def _stop(self) -> None:
        _LOGGER.info(
            "playback.stop attempt=%s position=%s",
            self._attempt_id,
            self._voice.position_seconds,
        )
        await self._change(Player.stop)
        self._stop_radio()
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
        _LOGGER.info(
            "playback.seek attempt=%s from=%s to=%.3f",
            self._attempt_id,
            self._voice.position_seconds,
            position_seconds,
        )
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
                paused=was_paused,
            )
        except TrackError as exc:
            await self._track_failed(exc)
            return
        self._resolved_track = track

    def _begin(
        self,
        *,
        retried: bool = False,
        position_seconds: float = 0,
        paused: bool = False,
        record_history: bool = True,
    ) -> None:
        entry = self._snapshot.current
        if entry is None:
            return
        attempt_id = uuid4()
        self._attempt_id = attempt_id
        self._position_origin = position_seconds
        self._retried = retried
        self._load_paused = paused
        if not retried:
            self._history_recorded = not record_history
        _LOGGER.info(
            "playback.loading attempt=%s entry=%s video=%s position=%.3f retry=%s paused=%s",
            attempt_id,
            entry.id,
            entry.video_id,
            position_seconds,
            retried,
            paused,
        )
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
            if track.duration_seconds is not None:
                self._position_origin = min(
                    self._position_origin, max(0, track.duration_seconds - 0.02)
                )
            try:
                self._voice.play(
                    track,
                    lambda error: self._completed_callback(attempt_id, error),
                    position_seconds=self._position_origin,
                    paused=self._load_paused,
                )
            except TrackError as exc:
                await self._track_failed(exc)
                return
            await self._change(
                lambda player: player.mark_playing(
                    record_history=not self._history_recorded,
                    paused=self._load_paused,
                )
            )
            self._load_paused = False
            self._history_recorded = True
            self._resolved_track = track
            _LOGGER.info(
                "playback.started attempt=%s entry=%s duration=%s opus=%s",
                attempt_id,
                entry.id,
                track.duration_seconds,
                track.is_opus,
            )

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
            _LOGGER.info(
                "playback.completed attempt=%s position=%s duration=%s error=%s",
                attempt_id,
                self._voice.position_seconds,
                self._resolved_track.duration_seconds if self._resolved_track else None,
                type(error).__name__ if error is not None else "none",
            )
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
        _LOGGER.warning(
            "playback.failed attempt=%s entry=%s position=%s retryable=%s action=%s",
            self._attempt_id,
            entry.id,
            self._voice.position_seconds,
            error.retryable,
            "retry" if retry else "skip",
        )
        position = self._position_origin if retry else 0
        paused = self._load_paused or self._snapshot.state is PlaybackState.PAUSED
        if not retry:
            self._last_issue = PlaybackIssue(
                entry.id,
                str(error),
                entry=entry,
                reason="stream_interrupted"
                if error.retryable
                else "source_unavailable",
            )
        await self._change(Player.fail)
        await self._halt()
        await self._change(Player.play if retry else Player.skip)
        self._begin(retried=retry, position_seconds=position, paused=paused)

    async def _halt(self) -> None:
        self._attempt_id = None
        self._load_paused = False
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
        _LOGGER.error(
            "playback.fault attempt=%s persist_failure=%s",
            self._attempt_id,
            persist_failure,
        )
        self._stop_radio()
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
        _LOGGER.info(
            "playback.closing attempt=%s position=%s",
            self._attempt_id,
            self._voice.position_seconds,
        )
        errors: list[Exception] = []
        self._checkpoint_task.cancel()
        await asyncio.gather(self._checkpoint_task, return_exceptions=True)
        self._stop_radio()
        await asyncio.gather(*tuple(self._radio_tasks), return_exceptions=True)
        if self.radio_catalog is not None:
            await self.radio_catalog.close()
        if self._metadata is not None:
            await self._metadata.close()
        if self.catalog is not None:
            await self.catalog.close()
        async with self._lock:
            try:
                if self._snapshot.state is PlaybackState.PLAYING:
                    self._voice.pause()
                await self._save_checkpoint()
            except Exception as exc:
                errors.append(exc)
            try:
                await self._halt()
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

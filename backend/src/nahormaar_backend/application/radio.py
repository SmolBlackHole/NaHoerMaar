# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Recommendation previews and Session-owned radio refill coordination."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from ..domain.catalog import CatalogTrack
from ..domain.models import (
    Contributor,
    PlaybackState,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from ..domain.radio import (
    RadioSeed,
    RadioEvent,
    RadioState,
    RadioStatus,
    radio_transition,
)
from .audio import TrackError
from .metadata import merge_metadata

RADIO_PREVIEW_LIMIT = 25
RADIO_POOL_LIMIT = 50
RADIO_BUFFER = 3
RADIO_PREVIEW_TTL = 600
RADIO_PREVIEWS = 32


class RecommendationProvider(Protocol):
    async def recommend(
        self, seed: RadioSeed, limit: int
    ) -> tuple[CatalogTrack, ...]: ...


@dataclass(frozen=True, slots=True)
class RadioPreview:
    id: UUID
    seed: RadioSeed
    entries: tuple[CatalogTrack, ...]


@dataclass(frozen=True, slots=True)
class RefillRadio:
    session_id: UUID | None


@dataclass(frozen=True, slots=True)
class RadioLoaded:
    session_id: UUID
    tracks: tuple[CatalogTrack, ...]
    error: str | None


type RadioMessage = RefillRadio | RadioLoaded


class RadioCatalog:
    def __init__(self, provider: RecommendationProvider) -> None:
        self.provider = provider
        self._previews: dict[UUID, tuple[UUID, float, RadioPreview]] = {}
        self._pending: dict[
            UUID, tuple[UUID, RadioSeed, asyncio.Task[RadioPreview]]
        ] = {}
        self._closed = False

    async def preview(
        self, identifier: UUID, owner: UUID, seed: RadioSeed
    ) -> RadioPreview:
        if self._closed:
            raise TrackError("Radio is unavailable.")
        self._previews = {
            key: value
            for key, value in self._previews.items()
            if value[1] > monotonic()
        }
        if identifier in self._previews:
            existing = self.get(identifier, owner)
            if existing.seed != seed:
                raise ValueError("This preview request was already used.")
            return existing
        pending = self._pending.get(identifier)
        if pending:
            if pending[:2] != (owner, seed):
                raise ValueError("This preview request was already used.")
            return await asyncio.shield(pending[2])
        if len(self._pending) >= 4:
            raise TrackError("Radio discovery is busy. Try again shortly.")

        async def fetch() -> RadioPreview:
            try:
                async with asyncio.timeout(35):
                    tracks = await self.provider.recommend(seed, RADIO_POOL_LIMIT)
                seen: set[str] = set()
                entries: list[CatalogTrack] = []
                for entry in tracks:
                    if (
                        entry.video_id
                        and entry.source_url
                        and not entry.unavailable
                        and entry.video_id not in seen
                    ):
                        seen.add(entry.video_id)
                        entries.append(entry)
                preview = RadioPreview(
                    identifier, seed, tuple(entries[:RADIO_PREVIEW_LIMIT])
                )
                if len(self._previews) >= RADIO_PREVIEWS:
                    self._previews.pop(next(iter(self._previews)))
                self._previews[identifier] = (
                    owner,
                    monotonic() + RADIO_PREVIEW_TTL,
                    preview,
                )
                return preview
            except TimeoutError:
                raise TrackError("Radio took too long. Try again.") from None
            finally:
                self._pending.pop(identifier, None)

        task = asyncio.create_task(fetch())
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._pending[identifier] = (owner, seed, task)
        return await asyncio.shield(task)

    def get(self, identifier: UUID, owner: UUID | None) -> RadioPreview:
        stored = self._previews.get(identifier)
        if stored is None or stored[0] != owner or stored[1] <= monotonic():
            raise ValueError("Open the radio preview again.")
        return stored[2]

    def metadata(self, video_id: str) -> TrackMetadata | None:
        for _, expiry, preview in reversed(tuple(self._previews.values())):
            if expiry > monotonic():
                for entry in preview.entries:
                    if entry.video_id == video_id:
                        return entry.metadata()
        return None

    async def close(self) -> None:
        self._closed = True
        tasks = [item[2] for item in self._pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._previews.clear()


class RadioController:
    def __init__(
        self,
        provider: RecommendationProvider | None,
        *,
        snapshot: Callable[[], PlayerSnapshot],
        source_key: Callable[[str], str],
        available: Callable[[], bool],
        send: Callable[[RadioMessage], Awaitable[None]],
        enqueue: Callable[[tuple[QueueEntry, ...]], Awaitable[None]],
        connected: Callable[[], bool],
        play: Callable[[], Awaitable[None]],
    ) -> None:
        self._provider = provider
        self._snapshot = snapshot
        self._source_key = source_key
        self._available = available
        self._send = send
        self._enqueue = enqueue
        self._connected = connected
        self._play = play
        self._status = RadioStatus()
        self._pool: tuple[CatalogTrack, ...] = ()
        self._removed: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._continuing = False

    @property
    def status(self) -> RadioStatus:
        return self._status

    def start(self, preview: RadioPreview, actor: Contributor) -> None:
        self.stop()
        self._status = RadioStatus(
            radio_transition(self._status.state, RadioEvent.START),
            uuid4(),
            preview.seed,
            actor,
            event_id=uuid4(),
            action="started",
            actor=actor,
        )
        self._pool = preview.entries
        self._removed.clear()
        self.observe_playback()

    def stop(self, actor: Contributor | None = None) -> None:
        if self._status.state is not RadioState.OFF:
            self._status = replace(
                self._status,
                state=radio_transition(self._status.state, RadioEvent.STOP),
                session_id=None,
                error=None,
                event_id=uuid4(),
                action="stopped",
                actor=actor,
            )
        self._pool = ()
        self._continuing = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    def retry(self, actor: Contributor | None) -> None:
        self._status = replace(
            self._status,
            state=radio_transition(self._status.state, RadioEvent.RETRY),
            error=None,
            event_id=uuid4(),
            action="retried",
            actor=actor,
        )

    def set_actor(self, actor: Contributor | None) -> None:
        self._status = replace(self._status, actor=actor)

    def remember_removals(self, entries: tuple[QueueEntry, ...]) -> None:
        if self._status.state is not RadioState.OFF:
            self._removed.update(
                entry.video_id or self._source_key(entry.source_url)
                for entry in entries
            )

    def observe_playback(self) -> None:
        """Retain committed playback intent before refill invalidations coalesce."""
        if self._status.state is not RadioState.OFF and self._snapshot().state in (
            PlaybackState.PLAYING,
            PlaybackState.LOADING,
        ):
            self._continuing = True

    async def handle(self, message: RadioMessage) -> None:
        """Apply a radio message inside the Session's ordered mutation boundary."""
        if self._closed or not self._available():
            return
        if message.session_id != self._status.session_id:
            return
        if isinstance(message, RefillRadio):
            await self._refill()
        else:
            await self._loaded(message)
        if (
            self._continuing
            and self._snapshot().current is None
            and self._snapshot().upcoming
            and self._connected()
        ):
            await self._play()

    async def _refill(self) -> None:
        if (
            self._task is not None
            or self._status.state is not RadioState.ACTIVE
            or self._snapshot().state is PlaybackState.PAUSED
            or len(self._snapshot().upcoming) >= RADIO_BUFFER
        ):
            return
        await self._enqueue_pool()
        if len(self._snapshot().upcoming) >= RADIO_BUFFER:
            return
        self._status = replace(
            self._status,
            state=radio_transition(self._status.state, RadioEvent.FETCH),
        )
        session, seed = self._status.session_id, self._status.seed
        if session is None or seed is None or self._provider is None:
            return
        task = asyncio.create_task(self._fetch(session, seed))
        self._task = task
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, completed: asyncio.Task[None]) -> None:
        self._tasks.discard(completed)
        if self._task is completed:
            self._task = None
        if not completed.cancelled():
            completed.exception()

    async def _enqueue_pool(self) -> int:
        snapshot = self._snapshot()
        active = snapshot.upcoming + ((snapshot.current,) if snapshot.current else ())
        excluded = self._removed | {
            entry.video_id or self._source_key(entry.source_url)
            for entry in (*active, *(item.entry for item in snapshot.recently_played))
        }
        entries: list[QueueEntry] = []
        remaining: list[CatalogTrack] = []
        for track in self._pool:
            if (
                not track.video_id
                or not track.source_url
                or track.unavailable
                or track.video_id in excluded
            ):
                continue
            excluded.add(track.video_id)
            if len(snapshot.upcoming) + len(entries) < RADIO_BUFFER:
                entries.append(
                    merge_metadata(
                        QueueEntry(
                            track.source_url,
                            added_by=self._status.initiator,
                            origin="radio",
                        ),
                        track.metadata(),
                    )
                )
            else:
                remaining.append(track)
        if entries:
            await self._enqueue(tuple(entries))
        self._pool = tuple(remaining)
        return len(entries)

    async def _fetch(self, session: UUID, seed: RadioSeed) -> None:
        if self._provider is None:
            return
        error: str | None = None
        tracks: tuple[CatalogTrack, ...] = ()
        try:
            async with asyncio.timeout(35):
                tracks = await self._provider.recommend(seed, RADIO_POOL_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception:
            error = "Radio could not load more tracks. Try again."

        await self._send(RadioLoaded(session, tracks, error))

    async def _loaded(self, message: RadioLoaded) -> None:
        if self._status.state is not RadioState.LOADING:
            return
        # A committed result can trigger the next refill before its sender has
        # returned. Task completion only reaps resources; it never schedules work.
        self._task = None
        if message.error:
            self._status = replace(
                self._status,
                state=radio_transition(self._status.state, RadioEvent.FAIL),
                error=message.error,
            )
            return
        self._pool = message.tracks[:RADIO_POOL_LIMIT]
        if self._snapshot().state is PlaybackState.PAUSED:
            self._status = replace(
                self._status,
                state=radio_transition(self._status.state, RadioEvent.READY),
            )
            return
        added = await self._enqueue_pool()
        exhausted = not added and len(self._snapshot().upcoming) < RADIO_BUFFER
        self._status = replace(
            self._status,
            state=radio_transition(
                self._status.state,
                RadioEvent.FAIL if exhausted else RadioEvent.READY,
            ),
            error="No new recommendations available. Try again later."
            if exhausted
            else None,
        )

    async def close(self) -> None:
        self._closed = True
        self.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

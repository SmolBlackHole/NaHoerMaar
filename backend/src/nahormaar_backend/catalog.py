# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded YouTube discovery and cancellable playlist previews."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

from .discovery_cache import Snapshot, SnapshotCache
from .audio import TrackError
from .models import Contributor, QueueEntry, TrackMetadata
from .processes import (
    ProcessOutputLimitError,
    ProcessResult,
    ProcessTimeoutError,
    run_process,
)
from .youtube import playlist_id, video_id
from .search import (
    CatalogBusy,
    CatalogTrack,
    SearchCatalog,
    SearchPage,
    SearchSource,
    YouTubeMusicSearch,
    YouTubeVideoSearch,
    metadata_object,
    metadata_text,
    catalog_track,
)

PLAYLIST_LIMIT = 100
DISCOVERY_CONCURRENCY = 2
DISCOVERY_PENDING_LIMIT = 8
PREVIEW_LIMIT = 16
PREVIEW_TTL = 600.0
METADATA_LIMIT = 1000


class PreviewState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PlaylistPreview:
    id: UUID
    source_url: str
    state: PreviewState = PreviewState.LOADING
    title: str | None = None
    entries: tuple[CatalogTrack, ...] = ()
    limit: int = PLAYLIST_LIMIT
    truncated: bool = False
    error: str | None = None
    snapshot_id: str | None = None
    refreshing: bool = False
    refresh_error: str | None = None


@dataclass(slots=True)
class _PreviewJob:
    snapshot: PlaylistPreview
    owner_id: UUID
    expires_at: float = field(default_factory=lambda: monotonic() + PREVIEW_TTL)
    task: asyncio.Task[None] | None = None


class MediaCatalog:
    def __init__(self, node_path: Path) -> None:
        self._node_path = node_path
        self._slots = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        self._tasks: set[asyncio.Task[object]] = set()
        self._previews: dict[UUID, _PreviewJob] = {}
        self._metadata = SnapshotCache[TrackMetadata](METADATA_LIMIT, 300.0)
        self._playlists = SnapshotCache[PlaylistPreview](32, 60.0)
        self._closed = False
        self._search = SearchCatalog(
            {
                SearchSource.MUSIC: YouTubeMusicSearch(self._execute),
                SearchSource.VIDEOS: YouTubeVideoSearch(self._run),
            },
            self._remember,
        )

    async def _run(
        self,
        source: str,
        options: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        args = (
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--no-cache-dir",
            "--no-plugin-dirs",
            "--no-remote-components",
            "--no-js-runtimes",
            "--js-runtimes",
            f"node:{self._node_path}",
            "--extractor-retries",
            "0",
            "--retries",
            "0",
            "--color",
            "never",
            "--simulate",
            *options,
            "--",
            source,
        )
        return await self._execute(args, timeout=timeout, on_line=on_line)

    async def _execute(
        self,
        args: tuple[str, ...],
        *,
        timeout: float = 30,
        on_line: Callable[[bytes], None] | None = None,
    ) -> ProcessResult:
        if self._closed or len(self._tasks) >= DISCOVERY_PENDING_LIMIT:
            raise CatalogBusy("Music discovery is busy. Try again shortly.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Discovery requires an asyncio task.")
        self._tasks.add(task)
        try:
            async with asyncio.timeout(timeout + 5), self._slots:
                return await run_process(args, timeout=timeout, on_stdout_line=on_line)
        except (TimeoutError, ProcessTimeoutError) as exc:
            raise TrackError("YouTube took too long. Try again.") from exc
        except ProcessOutputLimitError as exc:
            raise TrackError("YouTube returned too much metadata.") from exc
        except OSError as exc:
            raise TrackError("YouTube discovery could not be started.") from exc
        finally:
            self._tasks.discard(task)

    def _remember(self, entry: CatalogTrack) -> None:
        if entry.video_id and entry.unavailable is None:
            previous = self._metadata.peek(entry.video_id)
            values = asdict(previous.value) if previous else {}
            values.update(
                {
                    key: value
                    for key, value in asdict(entry.metadata()).items()
                    if value is not None
                }
            )
            self._metadata.put(entry.video_id, TrackMetadata(**values))

    def cached_metadata(self, source: str) -> TrackMetadata:
        identifier = video_id(source)
        item = self._metadata.peek(identifier or "")
        return item.value if item else TrackMetadata(video_id=identifier)

    def needs_refresh(self, source: str) -> bool:
        cached = self._metadata.peek(video_id(source) or "")
        return (
            cached is not None
            and monotonic() - cached.checked_at >= self._metadata.refresh_after
        )

    def queue_entry(self, source: str, added_by: Contributor | None) -> QueueEntry:
        return QueueEntry(
            source, added_by=added_by, **asdict(self.cached_metadata(source))
        )

    async def metadata(self, source_url: str) -> TrackMetadata:
        identifier = video_id(source_url)
        if identifier is None:
            raise TrackError("Use a YouTube video link.")
        cached = self._metadata.peek(identifier)
        incomplete = cached is not None and (
            cached.value.title is None or cached.value.duration_seconds is None
        )
        task = self._metadata.request(
            identifier, lambda: self._load_metadata(identifier), force=incomplete
        )
        if task is not None:
            try:
                return (await asyncio.shield(task)).value
            except (TrackError, CatalogBusy):
                if cached:
                    return cached.value
                raise
        return cast(Snapshot[TrackMetadata], cached).value

    async def _load_metadata(self, identifier: str) -> TrackMetadata:
        result = await self._run(
            f"https://www.youtube.com/watch?v={identifier}",
            (
                "--no-playlist",
                "--dump-single-json",
                "--ignore-no-formats-error",
                "--extractor-args",
                "youtube:player_skip=js;skip=hls,dash",
            ),
        )
        if result.returncode != 0:
            raise TrackError("YouTube could not load this track's details.")
        entry = catalog_track(metadata_object(result.stdout), 0)
        if entry.unavailable or entry.video_id != identifier:
            raise TrackError(entry.unavailable or "YouTube returned a different track.")
        return entry.metadata()

    async def search(
        self,
        query: str,
        offset: int = 0,
        source: SearchSource = SearchSource.MUSIC,
        snapshot_id: str | None = None,
    ) -> SearchPage:
        return await self._search.search(query, offset, source, snapshot_id)

    def _prune(self) -> None:
        now = monotonic()
        for identifier, job in tuple(self._previews.items()):
            if job.snapshot.state is not PreviewState.LOADING and job.expires_at <= now:
                del self._previews[identifier]

    def start_preview(
        self, source: str, identifier: UUID, *, owner_id: UUID
    ) -> PlaylistPreview:
        playlist = playlist_id(source)
        if playlist is None:
            raise ValueError("Use a YouTube playlist link.")
        source = f"https://www.youtube.com/playlist?list={playlist}"
        self._prune()
        if existing := self._previews.get(identifier):
            if existing.owner_id != owner_id or existing.snapshot.source_url != source:
                raise ValueError("This preview ID is already in use.")
            if existing.snapshot.state is PreviewState.READY:
                self._playlists.request(source, lambda: self._load_playlist(source))
                existing.expires_at = monotonic() + PREVIEW_TTL
            return self.preview(identifier, owner_id=owner_id)
        if self._closed or len(self._previews) >= PREVIEW_LIMIT:
            raise CatalogBusy("Too many playlist previews. Try again shortly.")
        job = _PreviewJob(PlaylistPreview(identifier, source), owner_id)
        cached = self._playlists.peek(source)
        if cached:
            job.snapshot = replace(cached.value, id=identifier, snapshot_id=cached.id)
        self._previews[identifier] = job
        job.task = asyncio.create_task(self._watch_preview(job))
        return replace(
            job.snapshot,
            refreshing=cached is not None
            and monotonic() - cached.checked_at >= self._playlists.refresh_after,
        )

    def preview(self, identifier: UUID, *, owner_id: UUID) -> PlaylistPreview:
        self._prune()
        job = self._previews[identifier]
        if job.owner_id != owner_id:
            raise KeyError(identifier)
        if job.snapshot.state is PreviewState.CANCELLED:
            return job.snapshot
        cached = self._playlists.peek(job.snapshot.source_url)
        if cached:
            job.snapshot = replace(cached.value, id=identifier, snapshot_id=cached.id)
        return replace(
            job.snapshot,
            refreshing=job.snapshot.source_url in self._playlists.pending,
            refresh_error=self._playlists.errors.get(job.snapshot.source_url),
        )

    async def _watch_preview(self, job: _PreviewJob) -> None:
        source = job.snapshot.source_url
        try:
            snapshot = await self._playlists.get(
                source, lambda: self._load_playlist(source)
            )
            if job.snapshot.state is not PreviewState.CANCELLED:
                job.snapshot = replace(
                    snapshot.value, id=job.snapshot.id, snapshot_id=snapshot.id
                )
                job.expires_at = monotonic() + PREVIEW_TTL
        except asyncio.CancelledError:
            raise
        except Exception:
            if job.snapshot.state is PreviewState.LOADING:
                job.snapshot = replace(
                    job.snapshot,
                    state=PreviewState.FAILED,
                    error="The playlist could not be loaded.",
                )

    async def _load_playlist(self, source: str) -> PlaylistPreview:
        snapshot = PlaylistPreview(uuid4(), source)
        cached = self._playlists.peek(source)

        def received(raw: bytes) -> None:
            nonlocal snapshot
            value = metadata_object(raw)
            if len(snapshot.entries) >= PLAYLIST_LIMIT:
                snapshot = replace(snapshot, truncated=True)
            else:
                entry = catalog_track(value, len(snapshot.entries) + 1)
                snapshot = replace(
                    snapshot,
                    entries=(*snapshot.entries, entry),
                    title=metadata_text(value.get("playlist_title")) or snapshot.title,
                )
            if cached is None:
                for waiting in self._previews.values():
                    if (
                        waiting.snapshot.source_url == source
                        and waiting.snapshot.state is PreviewState.LOADING
                    ):
                        waiting.snapshot = replace(snapshot, id=waiting.snapshot.id)

        error: str | None = None
        try:
            result = await self._run(
                source,
                (
                    "--yes-playlist",
                    "--flat-playlist",
                    "--lazy-playlist",
                    "--dump-json",
                    "--ignore-errors",
                    "--playlist-items",
                    f"1:{PLAYLIST_LIMIT + 1}",
                ),
                timeout=60,
                on_line=received,
            )
            if result.returncode != 0:
                error = "Some playlist entries could not be loaded."
        except asyncio.CancelledError:
            raise
        except (TrackError, CatalogBusy) as exc:
            error = str(exc)
        except Exception:
            error = "The playlist could not be loaded."
        if error and cached:
            raise TrackError(error)
        state = (
            PreviewState.FAILED
            if error and not snapshot.entries
            else PreviewState.READY
        )
        if state is PreviewState.FAILED:
            raise TrackError(error or "The playlist could not be loaded.")
        for entry in snapshot.entries:
            self._remember(entry)
        return replace(snapshot, id=UUID(int=0), state=state, error=error)

    async def cancel_preview(
        self, identifier: UUID, *, owner_id: UUID
    ) -> PlaylistPreview:
        self.preview(identifier, owner_id=owner_id)
        job = self._previews[identifier]
        if job.snapshot.state is PreviewState.LOADING:
            job.snapshot = replace(job.snapshot, state=PreviewState.CANCELLED)
            job.expires_at = monotonic() + PREVIEW_TTL
            if job.task is not None:
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)
            if not any(
                other.snapshot.source_url == job.snapshot.source_url
                and other.snapshot.state is PreviewState.LOADING
                for other in self._previews.values()
            ):
                await self._playlists.cancel(job.snapshot.source_url)
        return job.snapshot

    async def close(self) -> None:
        self._closed = True
        await self._search.close()
        await self._playlists.close()
        await self._metadata.close()
        tasks = {
            *self._tasks,
            *(job.task for job in self._previews.values() if job.task is not None),
        }
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._previews.clear()

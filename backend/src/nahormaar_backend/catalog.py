# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded YouTube discovery and cancellable playlist previews."""

from __future__ import annotations

import asyncio
import sys
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from time import monotonic
from uuid import UUID

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
        self._metadata: OrderedDict[str, tuple[float, TrackMetadata]] = OrderedDict()
        self._closed = False
        self._search = SearchCatalog(
            {
                SearchSource.MUSIC: YouTubeMusicSearch(self._execute),
                SearchSource.VIDEOS: YouTubeVideoSearch(self._run),
            }
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
            self._metadata[entry.video_id] = (
                monotonic() + PREVIEW_TTL,
                entry.metadata(),
            )
            self._metadata.move_to_end(entry.video_id)
            while len(self._metadata) > METADATA_LIMIT:
                self._metadata.popitem(last=False)

    def cached_metadata(self, source: str) -> TrackMetadata:
        identifier = video_id(source)
        item = self._metadata.get(identifier or "")
        if item is not None and item[0] > monotonic():
            return item[1]
        return TrackMetadata(video_id=identifier)

    def queue_entry(self, source: str, added_by: Contributor | None) -> QueueEntry:
        return QueueEntry(
            source, added_by=added_by, **asdict(self.cached_metadata(source))
        )

    async def metadata(self, source_url: str) -> TrackMetadata:
        source = source_url
        cached = self.cached_metadata(source)
        if cached.title is not None and cached.duration_seconds is not None:
            return cached
        if video_id(source) is None:
            raise TrackError("Use a YouTube video link.")
        result = await self._run(
            source,
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
        if entry.unavailable:
            raise TrackError(entry.unavailable)
        self._remember(entry)
        return entry.metadata()

    async def search(
        self, query: str, offset: int = 0, source: SearchSource = SearchSource.MUSIC
    ) -> SearchPage:
        page = await self._search.search(query, offset, source)
        for entry in page.entries:
            self._remember(entry)
        return page

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
            return existing.snapshot
        if self._closed or len(self._previews) >= PREVIEW_LIMIT:
            raise CatalogBusy("Too many playlist previews. Try again shortly.")
        job = _PreviewJob(PlaylistPreview(identifier, source), owner_id)
        self._previews[identifier] = job
        job.task = asyncio.create_task(self._load_preview(job))
        return job.snapshot

    def preview(self, identifier: UUID, *, owner_id: UUID) -> PlaylistPreview:
        self._prune()
        job = self._previews[identifier]
        if job.owner_id != owner_id:
            raise KeyError(identifier)
        return job.snapshot

    async def _load_preview(self, job: _PreviewJob) -> None:
        def received(raw: bytes) -> None:
            if job.snapshot.state is not PreviewState.LOADING:
                return
            value = metadata_object(raw)
            if len(job.snapshot.entries) >= PLAYLIST_LIMIT:
                job.snapshot = replace(job.snapshot, truncated=True)
                return
            entry = catalog_track(value, len(job.snapshot.entries) + 1)
            self._remember(entry)
            job.snapshot = replace(
                job.snapshot,
                entries=(*job.snapshot.entries, entry),
                title=metadata_text(value.get("playlist_title")) or job.snapshot.title,
            )

        error: str | None = None
        try:
            result = await self._run(
                job.snapshot.source_url,
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
            if job.snapshot.state is PreviewState.LOADING:
                job.snapshot = replace(job.snapshot, state=PreviewState.CANCELLED)
            raise
        except (TrackError, CatalogBusy) as exc:
            error = str(exc)
        except Exception:
            error = "The playlist could not be loaded."
        if job.snapshot.state is PreviewState.LOADING:
            state = (
                PreviewState.FAILED
                if error and not job.snapshot.entries
                else PreviewState.READY
            )
            job.snapshot = replace(job.snapshot, state=state, error=error)
            job.expires_at = monotonic() + PREVIEW_TTL

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
        return job.snapshot

    async def close(self) -> None:
        self._closed = True
        await self._search.close()
        tasks = {
            *self._tasks,
            *(job.task for job in self._previews.values() if job.task is not None),
        }
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._previews.clear()
        self._metadata.clear()

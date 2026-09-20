# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Search providers with one bounded, shared result cache."""

from __future__ import annotations

import asyncio
import json
import math
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from time import monotonic
from typing import Protocol, cast

from .audio import TrackError
from .models import TrackMetadata
from .processes import ProcessResult
from .youtube import video_id

SEARCH_LIMIT = 10
SEARCH_MAX_RESULTS = 100
SEARCH_CACHE_LIMIT = 32
SEARCH_CACHE_TTL = 300.0
SEARCH_PENDING_LIMIT = 8


class CatalogBusy(Exception):
    """Discovery is at capacity; playback has its own resolver."""


class SearchSource(StrEnum):
    MUSIC = "youtube_music"
    VIDEOS = "youtube"


@dataclass(frozen=True, slots=True)
class CatalogTrack(TrackMetadata):
    index: int = 0
    source_url: str | None = None
    unavailable: str | None = None

    def metadata(self) -> TrackMetadata:
        return TrackMetadata(
            **{key: getattr(self, key) for key in TrackMetadata.__dataclass_fields__}
        )


@dataclass(frozen=True, slots=True)
class SearchPage:
    entries: tuple[CatalogTrack, ...]
    next_offset: int | None


def metadata_object(raw: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise TrackError("YouTube returned invalid metadata.") from None
    if not isinstance(value, dict):
        raise TrackError("YouTube returned invalid metadata.")
    return cast(dict[str, object], value)


def metadata_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def catalog_track(value: dict[str, object], index: int) -> CatalogTrack:
    identifier = metadata_text(value.get("id"))
    source = f"https://www.youtube.com/watch?v={identifier}" if identifier else ""
    source = source if video_id(source) else ""
    duration = value.get("duration")
    duration = (
        float(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None
    )
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    title = metadata_text(value.get("track")) or metadata_text(value.get("title"))
    reason: str | None = None
    if (
        not source
        or metadata_text(value.get("availability"))
        in {"private", "premium_only", "subscriber_only", "needs_auth"}
        or title in {"[Private video]", "[Deleted video]"}
    ):
        reason = "This video is unavailable."
    elif value.get("is_live") is True or metadata_text(value.get("live_status")) in {
        "is_live",
        "is_upcoming",
        "post_live",
        "was_live",
    }:
        reason = "Live streams are not supported."
    thumbnail = metadata_text(value.get("thumbnail"))
    thumbnails = value.get("thumbnails")
    if thumbnail is None and isinstance(thumbnails, list):
        for candidate in cast(list[object], thumbnails):
            if isinstance(candidate, dict):
                thumbnail = (
                    metadata_text(cast(dict[str, object], candidate).get("url"))
                    or thumbnail
                )
    return CatalogTrack(
        index=index,
        source_url=source or None,
        video_id=identifier if source else None,
        title=title,
        artist=metadata_text(value.get("artist")),
        uploader=metadata_text(value.get("uploader"))
        or metadata_text(value.get("channel")),
        uploader_url=metadata_text(value.get("channel_url"))
        or metadata_text(value.get("uploader_url")),
        duration_seconds=duration,
        thumbnail_url=thumbnail,
        unavailable=reason,
    )


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]: ...


class YouTubeVideoSearch:
    def __init__(
        self,
        run: Callable[[str, tuple[str, ...]], Awaitable[ProcessResult]],
    ) -> None:
        self._run = run

    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]:
        result = await self._run(
            f"ytsearch{limit}:{query}", ("--flat-playlist", "--dump-single-json")
        )
        if result.returncode:
            raise TrackError("YouTube video search failed. Try again.")
        entries = metadata_object(result.stdout).get("entries")
        if not isinstance(entries, list):
            raise TrackError("YouTube returned invalid search results.")
        return tuple(
            catalog_track(
                cast(dict[str, object], item) if isinstance(item, dict) else {}, index
            )
            for index, item in enumerate(cast(list[object], entries)[:limit], 1)
        )


def music_track(value: dict[str, object], index: int) -> CatalogTrack:
    raw_artists = value.get("artists")
    artists = (
        [
            cast(dict[str, object], artist)
            for artist in cast(list[object], raw_artists)
            if isinstance(artist, dict)
        ]
        if isinstance(raw_artists, list)
        else []
    )
    names = [metadata_text(artist.get("name")) for artist in artists]
    artist_id = metadata_text(artists[0].get("id")) if artists else None
    entry = catalog_track(
        {
            "id": value.get("videoId"),
            "title": value.get("title"),
            "artist": ", ".join(dict.fromkeys(name for name in names if name)) or None,
            "channel_url": f"https://music.youtube.com/channel/{artist_id}"
            if artist_id
            else None,
            "duration": value.get("duration_seconds"),
            "thumbnails": value.get("thumbnails"),
            "availability": "private" if value.get("isAvailable") is False else None,
        },
        index,
    )
    return replace(
        entry,
        source_url=f"https://music.youtube.com/watch?v={entry.video_id}"
        if entry.video_id
        else None,
    )


class YouTubeMusicSearch:
    def __init__(
        self, run: Callable[[tuple[str, ...]], Awaitable[ProcessResult]]
    ) -> None:
        self._run = run

    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]:
        result = await self._run(
            (sys.executable, "-m", "nahormaar_backend.music_search", query, str(limit))
        )
        if result.returncode:
            raise TrackError("YouTube Music search failed. Try again or choose Videos.")
        entries = metadata_object(result.stdout).get("entries")
        if not isinstance(entries, list):
            raise TrackError("YouTube Music returned invalid search results.")
        return tuple(
            music_track(cast(dict[str, object], item), index)
            for index, item in enumerate(cast(list[object], entries)[:limit], 1)
            if isinstance(item, dict)
        )


class SearchCatalog:
    def __init__(self, providers: Mapping[SearchSource, SearchProvider]) -> None:
        self._providers = providers
        self._cache: OrderedDict[
            tuple[SearchSource, str], tuple[float, tuple[CatalogTrack, ...]]
        ] = OrderedDict()
        self._pending: dict[
            tuple[SearchSource, str], asyncio.Task[tuple[CatalogTrack, ...]]
        ] = {}
        self._closed = False

    async def search(
        self, query: str, offset: int = 0, source: SearchSource = SearchSource.MUSIC
    ) -> SearchPage:
        query = " ".join(query.split())
        if not 1 <= len(query) <= 200:
            raise ValueError("Search must contain 1 to 200 characters.")
        if offset < 0 or offset >= SEARCH_MAX_RESULTS or offset % SEARCH_LIMIT:
            raise ValueError("Invalid search offset.")
        if self._closed:
            raise CatalogBusy("Music discovery is closed.")
        key = (source, query.casefold())
        cached = self._cache.get(key)
        if cached is not None and cached[0] > monotonic():
            self._cache.move_to_end(key)
            tracks = cached[1]
        else:
            self._cache.pop(key, None)
            task = self._pending.get(key)
            if task is None:
                if len(self._pending) >= SEARCH_PENDING_LIMIT:
                    raise CatalogBusy("Music discovery is busy. Try again shortly.")
                task = asyncio.create_task(self._fetch(key, query))
                self._pending[key] = task
                task.add_done_callback(self._consume_failure)
            tracks = await asyncio.shield(task)
        end = offset + SEARCH_LIMIT
        return SearchPage(tracks[offset:end], end if len(tracks) > end else None)

    @staticmethod
    def _consume_failure(task: asyncio.Task[tuple[CatalogTrack, ...]]) -> None:
        if not task.cancelled():
            task.exception()

    async def _fetch(
        self, key: tuple[SearchSource, str], query: str
    ) -> tuple[CatalogTrack, ...]:
        try:
            tracks = (await self._providers[key[0]].search(query, SEARCH_MAX_RESULTS))[
                :SEARCH_MAX_RESULTS
            ]
            self._cache[key] = (monotonic() + SEARCH_CACHE_TTL, tracks)
            self._cache.move_to_end(key)
            while len(self._cache) > SEARCH_CACHE_LIMIT:
                self._cache.popitem(last=False)
            return tracks
        finally:
            self._pending.pop(key, None)

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._cache.clear()

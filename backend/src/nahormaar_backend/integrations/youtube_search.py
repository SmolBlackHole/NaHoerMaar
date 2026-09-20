# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""YouTube and YouTube Music search adapters and metadata parsing."""

import json
import math
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from ..application.audio import TrackError
from ..domain.catalog import CatalogTrack
from .processes import ProcessResult
from .youtube import video_id


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
            (
                sys.executable,
                "-m",
                "nahormaar_backend.integrations.music_search",
                query,
                str(limit),
            )
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

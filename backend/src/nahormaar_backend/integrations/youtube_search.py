# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""YouTube and YouTube Music search adapters and metadata parsing."""

import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

from ..application.audio import TrackError
from ..domain.catalog import CatalogTrack
from .processes import ProcessResult
from .youtube import video_id
from .youtube_metadata import metadata_text, track_metadata


def metadata_object(raw: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise TrackError("YouTube returned invalid metadata.") from None
    if not isinstance(value, dict):
        raise TrackError("YouTube returned invalid metadata.")
    return cast(dict[str, object], value)


def catalog_track(value: dict[str, object], index: int) -> CatalogTrack:
    metadata = track_metadata(value)
    identifier = metadata.video_id
    source = f"https://www.youtube.com/watch?v={identifier}" if identifier else ""
    source = source if video_id(source) else ""
    reason: str | None = None
    if (
        not source
        or metadata_text(value.get("availability"))
        in {"private", "premium_only", "subscriber_only", "needs_auth"}
        or metadata.title in {"[Private video]", "[Deleted video]"}
    ):
        reason = "This video is unavailable."
    elif value.get("is_live") is True or metadata_text(value.get("live_status")) in {
        "is_live",
        "is_upcoming",
        "post_live",
        "was_live",
    }:
        reason = "Live streams are not supported."
    return CatalogTrack(
        index=index,
        source_url=source or None,
        video_id=identifier if source else None,
        title=metadata.title,
        artist=metadata.artist,
        uploader=metadata.uploader,
        uploader_url=metadata.uploader_url,
        duration_seconds=metadata.duration_seconds,
        thumbnail_url=metadata.thumbnail_url,
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

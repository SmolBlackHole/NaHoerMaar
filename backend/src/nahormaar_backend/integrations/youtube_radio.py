# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Translate YouTube Music watch recommendations into public catalog tracks."""

import sys
from collections.abc import Awaitable, Callable
from typing import Literal, cast

from ..application.audio import TrackError
from ..domain.catalog import CatalogTrack
from ..domain.radio import RadioSeed
from .processes import ProcessResult
from .youtube import playlist_id, video_id
from .youtube_search import metadata_object, music_track


def radio_seed(
    source_url: str, kind: Literal["track", "playlist"], title: str
) -> RadioSeed:
    identifier = video_id(source_url) if kind == "track" else playlist_id(source_url)
    if identifier is None:
        raise ValueError("Use a public YouTube track or playlist link.")
    return RadioSeed(kind, identifier, title.strip() or "YouTube Music")


class YouTubeMusicRadio:
    def __init__(
        self, run: Callable[[tuple[str, ...]], Awaitable[ProcessResult]]
    ) -> None:
        self._run = run

    async def recommend(self, seed: RadioSeed, limit: int) -> tuple[CatalogTrack, ...]:
        result = await self._run(
            (
                sys.executable,
                "-m",
                "nahormaar_backend.integrations.music_radio",
                seed.kind,
                seed.identifier,
                str(limit),
            )
        )
        if result.returncode:
            raise TrackError("YouTube Music could not load this radio. Try again.")
        raw = metadata_object(result.stdout).get("tracks")
        if not isinstance(raw, list):
            raise TrackError("YouTube Music returned invalid radio results.")
        entries: list[CatalogTrack] = []
        for index, value in enumerate(cast(list[object], raw)[:limit], 1):
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, object], value)
            length = item.get("length")
            duration: int | None = None
            if isinstance(length, str):
                parts = length.split(":")
                if 1 <= len(parts) <= 3 and all(part.isdigit() for part in parts):
                    duration = sum(
                        int(part) * 60**power
                        for power, part in enumerate(reversed(parts))
                    )
            entries.append(
                music_track(
                    {
                        **item,
                        "duration_seconds": duration,
                        "thumbnails": item.get("thumbnail"),
                    },
                    index,
                )
            )
        return tuple(entries)

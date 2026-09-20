# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Public search results, independent of their provider."""

from dataclasses import dataclass
from enum import StrEnum

from .models import TrackMetadata


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
    snapshot_id: str
    latest_snapshot_id: str
    refreshing: bool = False
    refresh_error: str | None = None

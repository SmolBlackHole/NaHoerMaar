# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Public track metadata shared by YouTube playback and discovery."""

import math
from typing import cast

from ..domain.models import TrackMetadata


def metadata_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def track_metadata(value: dict[str, object]) -> TrackMetadata:
    duration = value.get("duration")
    duration = (
        float(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None
    )
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    thumbnail = metadata_text(value.get("thumbnail"))
    thumbnails = value.get("thumbnails")
    if thumbnail is None and isinstance(thumbnails, list):
        for candidate in cast(list[object], thumbnails):
            if isinstance(candidate, dict):
                thumbnail = (
                    metadata_text(cast(dict[str, object], candidate).get("url"))
                    or thumbnail
                )
    return TrackMetadata(
        video_id=metadata_text(value.get("id")),
        title=metadata_text(value.get("track")) or metadata_text(value.get("title")),
        uploader=metadata_text(value.get("uploader"))
        or metadata_text(value.get("channel")),
        duration_seconds=duration,
        thumbnail_url=thumbnail,
        artist=metadata_text(value.get("artist")),
        uploader_url=metadata_text(value.get("channel_url"))
        or metadata_text(value.get("uploader_url")),
    )

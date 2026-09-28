# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Domain values for cached and synchronized lyrics."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import re

from nahoermaar.catalog.domain import TrackId


class LyricsState(StrEnum):
    AVAILABLE = "available"
    INSTRUMENTAL = "instrumental"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class LyricLine:
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("Lyric line text must not be empty.")
        if self.start_seconds is None:
            if self.end_seconds is not None:
                raise ValueError("Untimed lyrics cannot have an end time.")
            return
        if self.start_seconds < 0:
            raise ValueError("Lyric line start must not be negative.")
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("Lyric line end must not precede its start.")


@dataclass(frozen=True, slots=True)
class TrackLyrics:
    track_id: TrackId
    metadata_signature: str
    state: LyricsState
    provider_record_id: int | None
    plain_lyrics: str | None
    synced_lyrics: str | None
    fetched_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.metadata_signature) != 64:
            raise ValueError("Lyrics metadata signature must be a SHA-256 digest.")
        if self.fetched_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("Lyrics cache times must be timezone-aware.")
        if self.expires_at.astimezone(UTC) <= self.fetched_at.astimezone(UTC):
            raise ValueError("Lyrics expiry must follow its fetch time.")
        if self.state is LyricsState.AVAILABLE:
            if self.provider_record_id is None or not (
                self.plain_lyrics or self.synced_lyrics
            ):
                raise ValueError("Available lyrics need a provider record and text.")
        elif self.state is LyricsState.INSTRUMENTAL:
            if self.provider_record_id is None:
                raise ValueError("Instrumental results need a provider record.")
            if self.plain_lyrics is not None or self.synced_lyrics is not None:
                raise ValueError("Instrumental results cannot contain lyrics.")
        elif (
            self.provider_record_id is not None
            or self.plain_lyrics is not None
            or self.synced_lyrics is not None
        ):
            raise ValueError("Missing lyrics cannot contain provider data.")

    def is_fresh(self, now: datetime, metadata_signature: str) -> bool:
        if now.utcoffset() is None:
            raise ValueError("Lyrics comparison time must be timezone-aware.")
        return (
            self.metadata_signature == metadata_signature
            and self.expires_at.astimezone(UTC) > now.astimezone(UTC)
        )

    def lines(self, duration_seconds: float | None) -> tuple[LyricLine, ...]:
        if self.synced_lyrics:
            parsed = parse_synced_lyrics(self.synced_lyrics, duration_seconds)
            if parsed:
                return parsed
        return plain_lyric_lines(self.plain_lyrics)


@dataclass(frozen=True, slots=True)
class LyricsResult:
    lyrics: TrackLyrics
    cached: bool
    stale: bool = False


_TIMESTAMP = re.compile(r"\[(?P<minutes>\d{1,3}):(?P<seconds>\d{2}(?:\.\d{1,3})?)\]")
_METADATA = re.compile(r"^\[[A-Za-z]{1,8}:")


def parse_synced_lyrics(
    value: str,
    duration_seconds: float | None = None,
) -> tuple[LyricLine, ...]:
    """Parse LRC line timestamps into a deterministic playback timeline."""
    timed: list[tuple[float, str]] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        timestamps = tuple(_TIMESTAMP.finditer(raw_line))
        if not timestamps:
            continue
        text = _TIMESTAMP.sub("", raw_line).strip()
        if not text or _METADATA.match(raw_line):
            continue
        for timestamp in timestamps:
            start = int(timestamp.group("minutes")) * 60 + float(
                timestamp.group("seconds")
            )
            timed.append((start, text))
    timed.sort(key=lambda item: item[0])
    return tuple(
        LyricLine(
            text,
            start,
            (
                timed[index + 1][0]
                if index + 1 < len(timed)
                else (
                    duration_seconds
                    if duration_seconds is not None and duration_seconds >= start
                    else None
                )
            ),
        )
        for index, (start, text) in enumerate(timed)
    )


def plain_lyric_lines(value: str | None) -> tuple[LyricLine, ...]:
    if not value:
        return ()
    return tuple(
        LyricLine(line.strip())
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    )

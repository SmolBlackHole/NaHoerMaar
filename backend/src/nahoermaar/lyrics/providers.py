# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Provider boundary owned by the Lyrics module."""

from dataclasses import dataclass
from typing import Protocol


class LyricsProviderError(RuntimeError):
    """One failed provider lookup with stable retry semantics."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class ProviderLyrics:
    """Lyrics metadata returned by one external provider."""

    record_id: int
    track_name: str
    artist_name: str
    album_name: str | None
    duration_seconds: float
    instrumental: bool
    plain_lyrics: str | None
    synced_lyrics: str | None

    def __post_init__(self) -> None:
        if self.record_id <= 0:
            raise ValueError("Lyrics record ID must be positive.")
        if not self.track_name.strip() or not self.artist_name.strip():
            raise ValueError("Lyrics track and artist names must be non-empty.")
        if self.duration_seconds <= 0:
            raise ValueError("Lyrics duration must be positive.")
        if self.instrumental and (
            self.plain_lyrics is not None or self.synced_lyrics is not None
        ):
            raise ValueError("Instrumental lyrics cannot contain text.")
        if not self.instrumental and not (self.plain_lyrics or self.synced_lyrics):
            raise ValueError("Non-instrumental lyrics need plain or synced text.")


class LyricsProvider(Protocol):
    """Resolve lyrics from canonical track metadata."""

    async def get(
        self,
        *,
        track_name: str,
        artist_name: str,
        album_name: str | None,
        duration_seconds: float | None,
    ) -> ProviderLyrics | None: ...

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Responsible LRCLIB HTTP client."""

import asyncio
from collections.abc import Mapping
import math
from time import monotonic
from typing import cast

import httpx

from nahoermaar.lyrics.providers import LyricsProviderError, ProviderLyrics

_CLIENT_ID = "NaHoerMaar/0.1.0 (https://github.com/SmolBlackHole/NaHoerMaar)"


class LrclibProvider:
    __slots__ = (
        "_base_url",
        "_last_request_at",
        "_lock",
        "_minimum_interval",
        "_transport",
    )

    def __init__(
        self,
        *,
        base_url: str = "https://lrclib.net",
        minimum_interval: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if minimum_interval < 0:
            raise ValueError("LRCLIB request interval must not be negative.")
        self._base_url = base_url.rstrip("/")
        self._minimum_interval = minimum_interval
        self._transport = transport
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    async def get(
        self,
        *,
        track_name: str,
        artist_name: str,
        album_name: str | None,
        duration_seconds: float | None,
    ) -> ProviderLyrics | None:
        params: dict[str, str | int] = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
        if album_name:
            params["album_name"] = album_name
        if duration_seconds is not None:
            params["duration"] = round(duration_seconds)
        async with self._lock:
            await self._throttle()
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url,
                    headers={"Lrclib-Client": _CLIENT_ID},
                    timeout=httpx.Timeout(10.0),
                    follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    response = await client.get("/api/get", params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                raise LyricsProviderError("lrclib_unavailable") from error
            finally:
                self._last_request_at = monotonic()
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            raise LyricsProviderError(
                "lrclib_rate_limited",
                retry_after_seconds=_retry_after(response.headers),
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise LyricsProviderError(
                "lrclib_failed",
                retryable=response.status_code >= 500,
            ) from error
        try:
            payload: object = response.json()
            return _lyrics(payload)
        except (TypeError, ValueError, KeyError) as error:
            raise LyricsProviderError("lrclib_invalid_response") from error

    async def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._minimum_interval - (monotonic() - self._last_request_at)
        if remaining > 0:
            await asyncio.sleep(remaining)


def _retry_after(headers: Mapping[str, str]) -> int | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _lyrics(payload: object) -> ProviderLyrics:
    if not isinstance(payload, Mapping):
        raise TypeError("LRCLIB response must be an object.")
    values = cast(Mapping[str, object], payload)
    record_id = values["id"]
    track_name = values["trackName"]
    artist_name = values["artistName"]
    duration = values["duration"]
    instrumental = values["instrumental"]
    album_name = values.get("albumName")
    plain_lyrics = values.get("plainLyrics")
    synced_lyrics = values.get("syncedLyrics")
    if (
        type(record_id) is not int
        or not isinstance(track_name, str)
        or not isinstance(artist_name, str)
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or type(instrumental) is not bool
        or (album_name is not None and not isinstance(album_name, str))
        or (plain_lyrics is not None and not isinstance(plain_lyrics, str))
        or (synced_lyrics is not None and not isinstance(synced_lyrics, str))
    ):
        raise TypeError("LRCLIB response has invalid fields.")
    return ProviderLyrics(
        record_id,
        track_name,
        artist_name,
        album_name,
        float(duration),
        instrumental,
        plain_lyrics or None,
        synced_lyrics or None,
    )

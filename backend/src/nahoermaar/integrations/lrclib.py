# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Responsible LRCLIB HTTP client."""

import asyncio
from collections.abc import Mapping
import math
import re
from time import monotonic
from typing import cast
import unicodedata

import httpx

from nahoermaar.lyrics.providers import LyricsProviderError, ProviderLyrics

_CLIENT_ID = "NaHoerMaar/0.1.0 (https://github.com/SmolBlackHole/NaHoerMaar)"
_MAX_SEARCH_RESULTS = 100
_DECORATION = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]\s*")
_SCRIPT_PREFIXES = (
    "ARABIC",
    "ARMENIAN",
    "CYRILLIC",
    "DEVANAGARI",
    "GEORGIAN",
    "GREEK",
    "HEBREW",
    "HIRAGANA",
    "KATAKANA",
    "LATIN",
    "THAI",
)


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
        params = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
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
                    response = await client.get("/api/search", params=params)
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
            candidates = _lyrics_candidates(payload)
            return _select_candidate(
                candidates,
                track_name=track_name,
                artist_name=artist_name,
                duration_seconds=duration_seconds,
            )
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


def _lyrics_candidates(payload: object) -> tuple[ProviderLyrics, ...]:
    if not isinstance(payload, list):
        raise TypeError("LRCLIB search response must be an array.")
    values = cast(list[object], payload)
    candidates: list[ProviderLyrics] = []
    for value in values[:_MAX_SEARCH_RESULTS]:
        try:
            candidates.append(_lyrics(value))
        except (TypeError, ValueError, KeyError):
            continue
    if values and not candidates:
        raise TypeError("LRCLIB search response contains no valid candidates.")
    return tuple(candidates)


def _select_candidate(
    candidates: tuple[ProviderLyrics, ...],
    *,
    track_name: str,
    artist_name: str,
    duration_seconds: float | None,
) -> ProviderLyrics | None:
    matches = [
        candidate
        for candidate in candidates
        if _match_score(track_name, candidate.track_name) > 0
        and _match_score(artist_name, candidate.artist_name) > 0
    ]
    if duration_seconds is not None:
        close_matches = [
            candidate
            for candidate in matches
            if abs(candidate.duration_seconds - duration_seconds) <= 3
        ]
        matches = close_matches or [
            candidate
            for candidate in matches
            if _match_score(track_name, candidate.track_name) == 2
            and _match_score(artist_name, candidate.artist_name) == 2
        ]
    if not matches:
        return None

    scripts: dict[str, list[ProviderLyrics]] = {}
    for candidate in matches:
        script = _dominant_script(candidate)
        if script is not None:
            scripts.setdefault(script, []).append(candidate)
    consensus = [group for group in scripts.values() if len(group) >= 2]
    if consensus:
        largest = max(len(group) for group in consensus)
        groups = [group for group in consensus if len(group) == largest]
        matches = min(
            groups,
            key=lambda group: min(
                _candidate_rank(
                    candidate,
                    track_name=track_name,
                    artist_name=artist_name,
                    duration_seconds=duration_seconds,
                )
                for candidate in group
            ),
        )

    return min(
        matches,
        key=lambda candidate: _candidate_rank(
            candidate,
            track_name=track_name,
            artist_name=artist_name,
            duration_seconds=duration_seconds,
        ),
    )


def _candidate_rank(
    candidate: ProviderLyrics,
    *,
    track_name: str,
    artist_name: str,
    duration_seconds: float | None,
) -> tuple[int, int, float, int, int]:
    distance = (
        abs(candidate.duration_seconds - duration_seconds)
        if duration_seconds is not None
        else 0
    )
    return (
        -_match_score(track_name, candidate.track_name),
        -_match_score(artist_name, candidate.artist_name),
        distance,
        0 if candidate.synced_lyrics else 1,
        candidate.record_id,
    )


def _match_score(requested: str, candidate: str) -> int:
    expected = _normalize(requested)
    full = _normalize(candidate)
    undecorated = _normalize(_DECORATION.sub(" ", candidate))
    if expected and expected in {full, undecorated}:
        return 2
    if expected and (expected in full or expected in undecorated):
        return 1
    return 0


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " " for character in normalized
        ).split()
    )


def _dominant_script(candidate: ProviderLyrics) -> str | None:
    lyrics = candidate.plain_lyrics or candidate.synced_lyrics
    if not lyrics:
        return None
    counts: dict[str, int] = {}
    total_letters = 0
    for character in lyrics:
        if not unicodedata.category(character).startswith("L"):
            continue
        total_letters += 1
        script = _script(character)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    if not counts or total_letters == 0:
        return None
    script, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return script if count / total_letters >= 0.7 else None


def _script(character: str) -> str | None:
    name = unicodedata.name(character, "")
    if name.startswith(("CJK ", "IDEOGRAPHIC ")):
        return "HAN"
    if name.startswith("HANGUL "):
        return "HANGUL"
    return next(
        (prefix for prefix in _SCRIPT_PREFIXES if name.startswith(prefix)), None
    )


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

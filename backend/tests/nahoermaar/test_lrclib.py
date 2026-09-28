# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

import httpx
import pytest

from nahoermaar.integrations.lrclib import LrclibProvider
from nahoermaar.lyrics.providers import LyricsProviderError


def test_lrclib_identifies_client_and_sends_complete_track_signature() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": 42,
                "trackName": "Still Alive",
                "artistName": "Mt Eden",
                "albumName": "Still Alive",
                "duration": 252,
                "instrumental": False,
                "plainLyrics": "First line",
                "syncedLyrics": "[00:01.00]First line",
            },
        )

    provider = LrclibProvider(
        minimum_interval=0,
        transport=httpx.MockTransport(respond),
    )
    result = asyncio.run(
        provider.get(
            track_name="Still Alive",
            artist_name="Mt Eden",
            album_name="Still Alive",
            duration_seconds=251.6,
        )
    )

    assert result is not None
    assert result.record_id == 42
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Lrclib-Client"].startswith("NaHoerMaar/0.1.0")
    assert request.url.params["track_name"] == "Still Alive"
    assert request.url.params["artist_name"] == "Mt Eden"
    assert request.url.params["album_name"] == "Still Alive"
    assert request.url.params["duration"] == "252"


@pytest.mark.parametrize("status", [404, 429, 503])
def test_lrclib_maps_missing_and_retryable_failures(status: int) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        headers = {"Retry-After": "17"} if status == 429 else None
        return httpx.Response(status, headers=headers)

    provider = LrclibProvider(
        minimum_interval=0,
        transport=httpx.MockTransport(respond),
    )

    async def lookup() -> object:
        return await provider.get(
            track_name="Track",
            artist_name="Artist",
            album_name=None,
            duration_seconds=None,
        )

    if status == 404:
        assert asyncio.run(lookup()) is None
        return
    with pytest.raises(LyricsProviderError) as raised:
        asyncio.run(lookup())
    assert raised.value.retryable
    assert raised.value.retry_after_seconds == (17 if status == 429 else None)


def test_lrclib_rejects_malformed_payloads() -> None:
    provider = LrclibProvider(
        minimum_interval=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"id": "wrong"})
        ),
    )

    with pytest.raises(LyricsProviderError, match="lrclib_invalid_response"):
        asyncio.run(
            provider.get(
                track_name="Track",
                artist_name="Artist",
                album_name=None,
                duration_seconds=180,
            )
        )

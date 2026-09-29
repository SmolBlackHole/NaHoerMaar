# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Callable

import httpx
import pytest

from nahoermaar.integrations.lrclib import LrclibProvider
from nahoermaar.lyrics.providers import LyricsProviderError, ProviderLyrics


def _candidate(
    record_id: int,
    *,
    track_name: str = "Still Alive",
    artist_name: str = "Mt Eden",
    duration: float = 252,
    plain_lyrics: str | None = "First line",
    synced_lyrics: str | None = "[00:01.00]First line",
    instrumental: bool = False,
) -> dict[str, object]:
    return {
        "id": record_id,
        "trackName": track_name,
        "artistName": artist_name,
        "albumName": "Album",
        "duration": duration,
        "instrumental": instrumental,
        "plainLyrics": plain_lyrics,
        "syncedLyrics": synced_lyrics,
    }


def _lookup(
    payload: object,
    *,
    track_name: str = "Still Alive",
    artist_name: str = "Mt Eden",
    duration_seconds: float | None = 251.6,
    inspect: Callable[[httpx.Request], None] | None = None,
) -> ProviderLyrics | None:
    def respond(request: httpx.Request) -> httpx.Response:
        if inspect is not None:
            inspect(request)
        return httpx.Response(200, json=payload)

    provider = LrclibProvider(
        minimum_interval=0,
        transport=httpx.MockTransport(respond),
    )
    return asyncio.run(
        provider.get(
            track_name=track_name,
            artist_name=artist_name,
            album_name="Album",
            duration_seconds=duration_seconds,
        )
    )


def test_lrclib_identifies_client_and_uses_one_search_request() -> None:
    requests: list[httpx.Request] = []
    result = _lookup([_candidate(42)], inspect=requests.append)

    assert result is not None
    assert result.record_id == 42
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/search"
    assert request.headers["Lrclib-Client"].startswith("NaHoerMaar/0.1.0")
    assert dict(request.url.params) == {
        "track_name": "Still Alive",
        "artist_name": "Mt Eden",
    }


def test_lrclib_prefers_repeated_cyrillic_script_for_yamakasi() -> None:
    result = _lookup(
        [
            _candidate(
                3_331_687,
                track_name="Yamakasi",
                artist_name="Miyagi & Andy Panda",
                duration=264,
                plain_lyrics="My little ghetto will not leave unanswered",
                synced_lyrics="[00:01.00]My little ghetto will not leave unanswered",
            ),
            _candidate(
                28_284_473,
                track_name="Yamakasi (NyKoto Remake)",
                artist_name="Miyagi & Andy Panda",
                duration=263.5,
                plain_lyrics="Мой маленький район не оставит без ответа",
                synced_lyrics="[00:01.00]Мой маленький район не оставит без ответа",
            ),
            _candidate(
                35_421_726,
                track_name="Yamakasi [Remix]",
                artist_name="Miyagi & Andy Panda (Kavkaz Music)",
                duration=263,
                plain_lyrics="Там, где дым уходит прямо в облака",
                synced_lyrics=None,
            ),
        ],
        track_name="Yamakasi",
        artist_name="Miyagi & Andy Panda",
        duration_seconds=264,
    )

    assert result is not None
    assert result.record_id == 28_284_473
    assert result.plain_lyrics is not None
    assert "Мой" in result.plain_lyrics


def test_lrclib_does_not_let_one_alternate_script_displace_exact_metadata() -> None:
    result = _lookup(
        [
            _candidate(1, duration=253, plain_lyrics="The exact result"),
            _candidate(
                2,
                track_name="Still Alive Translation",
                duration=251.6,
                plain_lyrics="Только один другой результат",
                synced_lyrics="[00:01.00]Только один другой результат",
            ),
        ]
    )

    assert result is not None
    assert result.record_id == 1


def test_lrclib_selects_one_candidate_without_duration() -> None:
    result = _lookup([_candidate(7)], duration_seconds=None)

    assert result is not None
    assert result.record_id == 7


def test_lrclib_falls_back_to_metadata_when_scripts_have_no_consensus() -> None:
    result = _lookup(
        [
            _candidate(11, plain_lyrics="An exact English result"),
            _candidate(
                12,
                track_name="Still Alive (Remix)",
                plain_lyrics="Один результат на кириллице",
            ),
            _candidate(
                13,
                track_name="Still Alive [Live]",
                plain_lyrics="Μόνο ένα ελληνικό αποτέλεσμα",
            ),
        ]
    )

    assert result is not None
    assert result.record_id == 11


def test_lrclib_skips_malformed_candidates_when_a_valid_one_remains() -> None:
    result = _lookup([{"id": "wrong"}, _candidate(42)])

    assert result is not None
    assert result.record_id == 42


def test_lrclib_accepts_instrumental_results() -> None:
    result = _lookup(
        [
            _candidate(
                9,
                instrumental=True,
                plain_lyrics=None,
                synced_lyrics=None,
            )
        ]
    )

    assert result is not None
    assert result.instrumental


def test_lrclib_returns_none_for_an_empty_search() -> None:
    assert _lookup([]) is None


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


@pytest.mark.parametrize("payload", [{"id": "wrong"}, [{"id": "wrong"}]])
def test_lrclib_rejects_malformed_payloads(payload: object) -> None:
    with pytest.raises(LyricsProviderError, match="lrclib_invalid_response"):
        _lookup(payload)

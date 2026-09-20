# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import json

import pytest

from nahormaar_backend.application.audio import ResolvedTrack, TrackError
from nahormaar_backend.domain.models import TrackMetadata
from nahormaar_backend.integrations import youtube
from nahormaar_backend.integrations.processes import ProcessResult
from nahormaar_backend.integrations.youtube_metadata import track_metadata
from nahormaar_backend.integrations.youtube_search import catalog_track

VIDEO_ID = "Pqp9fDRp1lw"
STREAM_URL = "https://stream.example/audio?signature=temporary"


def playback_track(value: dict[str, object]) -> ResolvedTrack:
    return youtube._resolved_track(  # pyright: ignore[reportPrivateUsage]
        ProcessResult(0, json.dumps(value, ensure_ascii=False).encode(), b"")
    )


@pytest.mark.parametrize("missing", [None, "", " \t ", 42, []])
def test_display_fallbacks_match_in_discovery_and_playback(missing: object) -> None:
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "duration": 180,
        "track": missing,
        "title": "Амура - Я хочу любить",
        "uploader": missing,
        "channel": "Music channel",
        "channel_url": missing,
        "uploader_url": "https://www.youtube.com/@music",
        "artist": missing,
        "thumbnail": missing,
        "thumbnails": [
            None,
            {"url": "https://i.ytimg.com/small.jpg"},
            {"url": 42},
            {"url": "https://i.ytimg.com/large.jpg"},
            {"url": " "},
            {},
        ],
        "url": STREAM_URL,
        "http_headers": {"User-Agent": "test"},
        "acodec": "opus",
    }
    expected = TrackMetadata(
        video_id=VIDEO_ID,
        title="Амура - Я хочу любить",
        uploader="Music channel",
        uploader_url="https://www.youtube.com/@music",
        duration_seconds=180,
        thumbnail_url="https://i.ytimg.com/large.jpg",
    )

    assert track_metadata(value) == expected
    assert catalog_track(value, 1).metadata() == expected
    resolved = playback_track(value)
    assert resolved.metadata == expected
    assert resolved.stream_url == STREAM_URL
    assert resolved.headers == (("User-Agent", "test"),)
    assert resolved.is_opus


def test_preferred_metadata_is_preserved_in_both_adapters() -> None:
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "duration": 180.5,
        "track": "  Амура  ",
        "title": "Different upload title",
        "artist": "Artist, Guest",
        "uploader": "Uploader",
        "channel": "Fallback channel",
        "channel_url": "https://www.youtube.com/channel/primary",
        "uploader_url": "https://www.youtube.com/@fallback",
        "thumbnail": "https://i.ytimg.com/primary.jpg",
        "thumbnails": [{"url": "https://i.ytimg.com/fallback.jpg"}],
        "url": STREAM_URL,
    }
    expected = TrackMetadata(
        video_id=VIDEO_ID,
        title="  Амура  ",
        artist="Artist, Guest",
        uploader="Uploader",
        uploader_url="https://www.youtube.com/channel/primary",
        duration_seconds=180.5,
        thumbnail_url="https://i.ytimg.com/primary.jpg",
    )
    assert catalog_track(value, 1).metadata() == expected
    assert playback_track(value).metadata == expected


def test_missing_metadata_has_no_invented_fallbacks() -> None:
    assert track_metadata({}) == TrackMetadata()
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "duration": 180,
        "artist": "Artist",
        "thumbnails": {"url": "https://i.ytimg.com/not-a-list.jpg"},
        "url": STREAM_URL,
    }
    expected = TrackMetadata(video_id=VIDEO_ID, duration_seconds=180, artist="Artist")
    assert catalog_track(value, 1).metadata() == expected
    assert playback_track(value).metadata == expected


@pytest.mark.parametrize(
    "duration",
    [None, True, False, 0, -1, float("nan"), float("inf"), -float("inf"), "180"],
)
def test_unknown_duration_is_displayable_but_not_playable(duration: object) -> None:
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "duration": duration,
        "title": "Song",
        "url": STREAM_URL,
    }
    entry = catalog_track(value, 1)
    assert entry.duration_seconds is None
    assert entry.title == "Song" and entry.unavailable is None
    with pytest.raises(TrackError, match="livestreams"):
        playback_track(value)


@pytest.mark.parametrize(
    "live",
    [
        {"is_live": True},
        {"live_status": "is_live"},
        {"live_status": "is_upcoming"},
        {"live_status": "post_live"},
        {"live_status": "was_live"},
    ],
)
def test_live_entries_remain_visible_but_unplayable(live: dict[str, object]) -> None:
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "title": "Live song",
        "duration": 180,
        "url": STREAM_URL,
        **live,
    }
    entry = catalog_track(value, 1)
    assert entry.title == "Live song"
    assert entry.unavailable == "Live streams are not supported."
    with pytest.raises(TrackError, match="livestreams"):
        playback_track(value)


@pytest.mark.parametrize(
    ("stream", "message"),
    [
        ({"url": None}, "no playable audio stream"),
        ({"url": "not-a-url"}, "no playable audio stream"),
        ({"url": "file:///audio"}, "no playable audio stream"),
        (
            {"url": "https://user:password@stream.example/audio"},
            "no playable audio stream",
        ),
        ({"http_headers": {"Invalid\nHeader": "test"}}, "invalid stream metadata"),
        (
            {"http_headers": {"User-Agent": "test\r\nOther: value"}},
            "invalid stream metadata",
        ),
    ],
)
def test_stream_validation_remains_specific_to_playback(
    stream: dict[str, object], message: str
) -> None:
    value: dict[str, object] = {
        "id": VIDEO_ID,
        "duration": 180,
        "title": "Song",
        "url": STREAM_URL,
        **stream,
    }
    assert catalog_track(value, 1).unavailable is None
    with pytest.raises(TrackError, match=message):
        playback_track(value)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys
from typing import cast
from uuid import UUID, uuid4

import pytest

from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import (
    Artist,
    ArtistIdentity,
    MediaIdentity,
    Track,
)

CATALOG_TIME = datetime(2026, 9, 21, tzinfo=UTC)


def test_media_identity_is_namespaced_and_case_sensitive() -> None:
    video = MediaIdentity("youtube", "GCYGuZGE6DA")
    assert video == MediaIdentity("youtube", "GCYGuZGE6DA")
    assert video != MediaIdentity("youtube", "gcyguzge6da")
    assert video != MediaIdentity("another-source", "GCYGuZGE6DA")
    assert {video: "known track"}[MediaIdentity("youtube", "GCYGuZGE6DA")] == (
        "known track"
    )


@pytest.mark.parametrize("value", ["", " ", " padded", "padded ", "\t\n"])
def test_identity_and_source_reference_reject_empty_or_untrimmed_values(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="namespace"):
        MediaIdentity(value, "GCYGuZGE6DA")
    with pytest.raises(ValueError, match="external_id"):
        MediaIdentity("youtube", value)
    with pytest.raises(ValueError, match="source_url"):
        Track(
            MediaIdentity("youtube", "GCYGuZGE6DA"),
            value,
            created_at=CATALOG_TIME,
            updated_at=CATALOG_TIME,
        )


def test_metadata_refresh_preserves_track_identity_and_source() -> None:
    track = Track(
        identity=MediaIdentity("youtube", "GCYGuZGE6DA"),
        source_url="https://www.youtube.com/watch?v=GCYGuZGE6DA",
        metadata=TrackMetadata(title="Амура - Я хочу любить (MRJay Remix)"),
        id=UUID("00000000-0000-0000-0000-000000000001"),
        created_at=CATALOG_TIME,
        updated_at=CATALOG_TIME,
    )
    updated = replace(
        track,
        metadata=track.metadata.merge(
            TrackMetadata(artist="Амура", duration_seconds=240)
        ),
    )
    assert updated.id == track.id
    assert updated.identity == track.identity
    assert updated.source_url == track.source_url
    assert updated.created_at == updated.updated_at == CATALOG_TIME
    assert updated.checked_at is None
    assert updated.metadata.title == "Амура - Я хочу любить (MRJay Remix)"
    assert updated.metadata.artist == "Амура"
    assert track.metadata.artist is None


def test_partial_search_metadata_fills_gaps_without_overwriting_known_values() -> None:
    existing = TrackMetadata(
        title="Original title", artist="Artist, with comma", duration_seconds=245.5
    )
    search = TrackMetadata(
        title="Less detailed title", duration_seconds=245, uploader="Uploader"
    )
    assert existing.merge(search) == TrackMetadata(
        title="Original title",
        artist="Artist, with comma",
        duration_seconds=245.5,
        uploader="Uploader",
    )


def test_explicit_refresh_never_erases_missing_fields() -> None:
    existing = TrackMetadata(title="Old title", artist="Artist", duration_seconds=120)
    refreshed = existing.merge(
        TrackMetadata(title="Updated title", duration_seconds=0), overwrite=True
    )
    assert refreshed == TrackMetadata(
        title="Updated title", artist="Artist", duration_seconds=0
    )
    assert existing.title == "Old title"


@pytest.mark.parametrize("overwrite", [False, True])
def test_all_known_fields_survive_an_empty_observation(overwrite: bool) -> None:
    complete = TrackMetadata(
        title="Track",
        artist="Artist",
        uploader="Uploader",
        uploader_url="https://www.youtube.com/@artist",
        duration_seconds=0,
        thumbnail_url="https://i.ytimg.com/vi/GCYGuZGE6DA/hqdefault.jpg",
    )
    assert complete.merge(TrackMetadata(), overwrite=overwrite) == complete
    assert TrackMetadata().merge(complete) == complete
    assert complete.merge(TrackMetadata(duration_seconds=120)).duration_seconds == 0


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_duration_cannot_enter_shared_metadata(duration: float) -> None:
    with pytest.raises(ValueError, match="Duration"):
        TrackMetadata(duration_seconds=duration)


@pytest.mark.parametrize("value", ["", " ", " padded", "padded "])
@pytest.mark.parametrize(
    "name", ["title", "artist", "uploader", "uploader_url", "thumbnail_url"]
)
def test_blank_metadata_must_be_mapped_to_unknown(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        replace(TrackMetadata(), **{name: value})  # type: ignore[arg-type]


def test_track_and_metadata_are_immutable() -> None:
    track = Track(
        MediaIdentity("youtube", "GCYGuZGE6DA"),
        "https://www.youtube.com/watch?v=GCYGuZGE6DA",
        created_at=CATALOG_TIME,
        updated_at=CATALOG_TIME,
    )
    for target, attribute, value in (
        (track, "id", UUID(int=0)),
        (track.identity, "external_id", "different"),
        (track.metadata, "title", "Changed"),
        (track, "created_at", CATALOG_TIME + timedelta(seconds=1)),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)


def test_domain_imports_without_legacy_core_or_io_libraries() -> None:
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(  # noqa: S603 - fixed isolated Python import probe
        [
            sys.executable,
            "-I",
            "-c",
            "import json, sys; sys.path.insert(0, sys.argv[1]); "
            "from nahormaar_backend.engine.domain.tracks import Track; "
            "from nahormaar_backend.engine.domain.queue import QueueEntry; "
            "from nahormaar_backend.engine.domain.sessions import PlaybackCheckpoint; "
            "from nahormaar_backend.engine.domain.playback import decide; "
            "from nahormaar_backend.engine.providers import Provider; "
            "from nahormaar_backend.engine.audio import AudioPlayer, VoiceTransport; "
            "domain_modules = sorted(sys.modules); "
            "from nahormaar_backend.engine.metadata import MetadataStore; "
            "from nahormaar_backend.engine.catalog import Catalog; "
            "from nahormaar_backend.engine.session import Session; "
            "core_modules = sorted(sys.modules); "
            "from nahormaar_backend.engine.youtube import YouTubeProvider, YouTubeMusicProvider; "
            "adapter_modules = sorted(sys.modules); "
            "from nahormaar_backend.engine.api import create_app; "
            "from nahormaar_backend.engine.commands import DiscordCommands; "
            "import nahormaar_backend.__main__; "
            "print(json.dumps([domain_modules, core_modules, adapter_modules, sorted(sys.modules)]))",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    module_groups: list[list[str]] = json.loads(result.stdout)
    loaded, with_storage, with_adapter, with_api = module_groups
    assert "nahormaar_backend.engine.domain.tracks" in loaded
    assert "nahormaar_backend.engine.domain.queue" in loaded
    assert "nahormaar_backend.engine.domain.sessions" in loaded
    assert "nahormaar_backend.engine.domain.catalog" in loaded
    assert "nahormaar_backend.engine.providers" in loaded
    assert "nahormaar_backend.engine.audio" in loaded
    shared_domain = {
        "nahormaar_backend.domain",
        "nahormaar_backend.domain.identity",
    }
    assert not any(
        name.startswith("nahormaar_backend.")
        and not name.startswith("nahormaar_backend.engine")
        and name not in shared_domain
        for name in with_storage
    )
    assert not {"discord", "sqlalchemy", "httpx", "yt_dlp", "ytmusicapi"}.intersection(
        loaded
    )
    assert "nahormaar_backend.engine.metadata" in with_storage
    assert "nahormaar_backend.engine.catalog" in with_storage
    assert "nahormaar_backend.engine.youtube" not in with_storage
    assert not {"discord", "httpx", "yt_dlp", "ytmusicapi"}.intersection(with_storage)
    technical_modules = {
        "nahormaar_backend.integrations",
        "nahormaar_backend.integrations.processes",
        "nahormaar_backend.integrations.ytdlp",
    }
    assert not any(
        name.startswith("nahormaar_backend.")
        and not name.startswith("nahormaar_backend.engine")
        and name not in technical_modules | shared_domain
        for name in with_adapter
    )
    assert not {"discord", "httpx", "yt_dlp", "ytmusicapi"}.intersection(with_adapter)
    assert not {
        "nahormaar_backend.runtime",
        "nahormaar_backend.api",
        "nahormaar_backend.application.session",
        "nahormaar_backend.application.player",
        "nahormaar_backend.application.playback",
        "nahormaar_backend.application.catalog",
        "nahormaar_backend.application.radio",
        "nahormaar_backend.persistence.player_store",
    }.intersection(with_api)


def test_track_timestamps_require_aware_values_and_ordered_creation_and_update() -> (
    None
):
    track = Track(
        MediaIdentity("youtube", "video"),
        "https://www.youtube.com/watch?v=video",
        created_at=CATALOG_TIME,
        updated_at=CATALOG_TIME,
    )
    naive = datetime(2026, 9, 21)
    with pytest.raises(ValueError, match="created_at must be timezone-aware"):
        replace(track, created_at=naive)
    with pytest.raises(ValueError, match="updated_at must be timezone-aware"):
        replace(track, updated_at=naive)
    with pytest.raises(ValueError, match="checked_at must be timezone-aware"):
        replace(track, checked_at=naive)
    with pytest.raises(ValueError, match="cannot precede"):
        replace(track, updated_at=CATALOG_TIME - timedelta(seconds=1))
    # This later wall-clock value is earlier in UTC.
    with pytest.raises(ValueError, match="cannot precede"):
        replace(
            track,
            updated_at=datetime(2026, 9, 21, 1, tzinfo=timezone(timedelta(hours=2))),
        )
    assert (
        replace(track, checked_at=CATALOG_TIME - timedelta(seconds=1)).checked_at
        is not None
    )
    assert (
        replace(track, checked_at=CATALOG_TIME + timedelta(seconds=1)).updated_at
        == CATALOG_TIME
    )


@pytest.mark.parametrize("value", ["", " ", " padded", "padded "])
def test_artists_require_an_explicit_identity_and_trimmed_name(value: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        ArtistIdentity(value, "UCArtist")
    with pytest.raises(ValueError, match="external_id"):
        ArtistIdentity("youtube", value)
    with pytest.raises(ValueError, match="name"):
        Artist(ArtistIdentity("youtube", "UCArtist"), value)


def test_equal_artist_names_do_not_define_identity() -> None:
    first = Artist(ArtistIdentity("youtube", "UCArtist"), "Artist, with comma")
    second = Artist(ArtistIdentity("youtube", "ucartist"), first.name)
    third = Artist(ArtistIdentity("another-source", "UCArtist"), first.name)
    assert len({first.identity, second.identity, third.identity}) == 3
    assert len({first.id, second.id, third.id}) == 3
    assert replace(first, name="Имя").identity == first.identity
    for target, attribute in ((first, "name"), (first.identity, "external_id")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, "Changed")


def test_credits_are_unique_ordered_references_without_rewriting_artist_text() -> None:
    first, second = uuid4(), uuid4()
    track = Track(
        MediaIdentity("youtube", "video"),
        "https://www.youtube.com/watch?v=video",
        TrackMetadata(artist="Artist, with comma & another artist"),
        created_at=CATALOG_TIME,
        updated_at=CATALOG_TIME,
        artist_ids=(first, second),
    )
    reordered = replace(track, artist_ids=(second, first))
    assert reordered.artist_ids == (second, first)
    assert reordered.metadata.artist == track.metadata.artist
    assert replace(track, artist_ids=()).metadata.artist == track.metadata.artist
    with pytest.raises(ValueError, match="same artist twice"):
        replace(track, artist_ids=(first, first))
    with pytest.raises(ValueError, match="immutable tuple"):
        replace(track, artist_ids=cast(tuple[UUID, ...], [first, second]))

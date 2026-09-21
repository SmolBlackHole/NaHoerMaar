# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from nahormaar_backend.engine.domain.catalog import (
    ArtistCredit,
    MediaKind,
    MediaReference,
    PlaylistPage,
    TrackFinding,
    TrackPage,
    UnavailableFinding,
)
from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import ArtistIdentity, MediaIdentity
from nahormaar_backend.engine.providers import (
    PlaybackProvider,
    PlaylistProvider,
    Provider,
    ProviderError,
    RadioProvider,
    SearchProvider,
    TrackProvider,
    UnsupportedCapability,
)

REFERENCE = MediaReference(
    MediaIdentity("youtube", "GCYGuZGE6DA"),
    MediaKind.TRACK,
    "https://www.youtube.com/watch?v=GCYGuZGE6DA",
)


def test_provider_findings_preserve_metadata_and_external_artist_identity() -> None:
    first = ArtistCredit(ArtistIdentity("youtube", "first"), "Same name")
    second = ArtistCredit(ArtistIdentity("youtube", "second"), "Same name")
    finding = TrackFinding(
        REFERENCE,
        TrackMetadata(title="Я хочу любить", artist="Do not split, this name"),
        (first, second),
    )
    assert finding.artists == (first, second)
    assert finding.metadata.artist == "Do not split, this name"
    assert finding.reference.identity == MediaIdentity("youtube", "GCYGuZGE6DA")
    assert TrackFinding(REFERENCE).artists is None
    assert TrackFinding(REFERENCE, artists=()).artists == ()
    with pytest.raises(ValueError, match="same artist twice"):
        replace(finding, artists=(first, first))
    for target, name in ((finding, "metadata"), (first, "name"), (REFERENCE, "kind")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, None)


def test_playlist_occurrences_survive_duplicates_missing_ids_and_partial_results() -> (
    None
):
    song = TrackFinding(REFERENCE, TrackMetadata(title="Track"))
    unavailable = UnavailableFinding(
        "This video is unavailable.", TrackMetadata(title="[Deleted video]")
    )
    reference = MediaReference(
        MediaIdentity("youtube", "PLfixture"),
        MediaKind.PLAYLIST,
        "https://www.youtube.com/playlist?list=PLfixture",
    )
    playlist = PlaylistPage(
        reference,
        "Songs",
        TrackPage((song, unavailable, song), "opaque / cursor==", "Partial load."),
    )
    assert playlist.page.entries == (song, unavailable, song)
    assert unavailable.reference is None
    assert playlist.page.continuation == "opaque / cursor=="
    assert playlist.page.error == "Partial load."
    assert replace(playlist.page, entries=(), continuation=None).entries == ()
    with pytest.raises(ValueError, match="track reference"):
        TrackFinding(reference)
    with pytest.raises(ValueError, match="track reference"):
        UnavailableFinding("Unavailable", reference=reference)
    with pytest.raises(ValueError, match="playlist reference"):
        replace(playlist, reference=REFERENCE)


@pytest.mark.parametrize("value", ["", " ", " padded", "padded "])
def test_adapters_must_normalize_blank_and_padded_values(value: str) -> None:
    with pytest.raises(ValueError, match="source_url"):
        replace(REFERENCE, source_url=value)
    with pytest.raises(ValueError, match="name"):
        ArtistCredit(ArtistIdentity("youtube", "artist"), value)
    with pytest.raises(ValueError, match="reason"):
        UnavailableFinding(value)
    with pytest.raises(ValueError, match="error"):
        TrackPage((), error=value)


def test_empty_cursor_cannot_be_confused_with_exhaustion() -> None:
    assert TrackPage(()).continuation is None
    with pytest.raises(ValueError, match="exhausted"):
        TrackPage((), continuation="")


class SearchOnly:
    """A structural test double, with no forced playlist/radio/audio methods."""

    key = "youtube_music"

    def identify(
        self, source_url: str, *, kind: MediaKind | None = None
    ) -> MediaReference | None:
        return None

    async def search(
        self, query: str, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        return TrackPage((TrackFinding(REFERENCE, TrackMetadata(title=query)),))

    async def close(self) -> None:
        pass


def test_capabilities_are_optional_and_provider_key_is_not_media_identity() -> None:
    provider: SearchProvider = SearchOnly()  # Also checked by mypy/Pyright.
    assert isinstance(provider, Provider)
    assert isinstance(provider, SearchProvider)
    assert not isinstance(provider, TrackProvider)
    assert not isinstance(provider, PlaylistProvider)
    assert not isinstance(provider, RadioProvider)
    assert not isinstance(provider, PlaybackProvider)
    page = asyncio.run(provider.search("Song", limit=10))
    song = page.entries[0]
    assert isinstance(song, TrackFinding)
    assert provider.key == "youtube_music"
    assert song.reference.identity.namespace == "youtube"
    assert song.metadata.title == "Song"


def test_provider_failures_distinguish_transient_errors_from_unsupported_work() -> None:
    assert ProviderError("Temporary timeout", retryable=True).retryable
    assert not ProviderError("Unavailable track").retryable
    assert not UnsupportedCapability("No radio capability").retryable

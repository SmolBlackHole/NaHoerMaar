# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from nahormaar_backend.engine.domain.metadata import (
    MetadataKind,
    MetadataProvenance,
    MetadataSource,
    TrackMetadata,
)

TIME = datetime(2026, 9, 21, 12, tzinfo=UTC)
DISCOVERY = MetadataSource("youtube_music", MetadataKind.DISCOVERY, TIME)
DETAIL = MetadataSource("youtube", MetadataKind.DETAIL, TIME)


@pytest.mark.parametrize("kind", list(MetadataKind))
def test_observations_fill_gaps_and_record_each_fields_source(
    kind: MetadataKind,
) -> None:
    source = replace(DISCOVERY, kind=kind)
    observed = TrackMetadata(title="Я хочу любить", duration_seconds=0)
    metadata, provenance = TrackMetadata().merge_observation(
        observed, MetadataProvenance(), source
    )
    assert metadata == observed
    assert provenance == MetadataProvenance(title=source, duration_seconds=source)
    assert provenance.artists is None


def test_discovery_cannot_replace_details_but_can_fill_other_fields() -> None:
    detailed = TrackMetadata(title="Full title", duration_seconds=180)
    provenance = MetadataProvenance(title=DETAIL, duration_seconds=DETAIL)
    search = replace(DISCOVERY, observed_at=TIME + timedelta(days=1))
    metadata, accepted = detailed.merge_observation(
        TrackMetadata(
            title="Truncated title", artist="New artist", duration_seconds=179
        ),
        provenance,
        search,
    )
    assert metadata == replace(detailed, artist="New artist")
    assert accepted == replace(provenance, artist=search)


@pytest.mark.parametrize("seconds", [-1, 0, 1])
@pytest.mark.parametrize("kind", list(MetadataKind))
def test_equal_quality_requires_a_later_observation(
    kind: MetadataKind, seconds: int
) -> None:
    previous = MetadataSource("first", kind, TIME)
    incoming = MetadataSource("second", kind, TIME + timedelta(seconds=seconds))
    before = TrackMetadata(title="First")
    provenance = MetadataProvenance(title=previous)
    metadata, evidence = before.merge_observation(
        TrackMetadata(title="Second"), provenance, incoming
    )
    assert metadata.title == ("Second" if seconds > 0 else "First")
    assert evidence.title == (incoming if seconds > 0 else previous)


def test_detail_upgrades_discovery_even_if_the_discovery_finished_later() -> None:
    search = replace(DISCOVERY, observed_at=TIME + timedelta(seconds=5))
    metadata, provenance = TrackMetadata(title="Search title").merge_observation(
        TrackMetadata(title="Full title"), MetadataProvenance(title=search), DETAIL
    )
    assert metadata.title == "Full title" and provenance.title == DETAIL


def test_none_does_not_erase_content_or_claim_provenance_and_zero_is_known() -> None:
    original = TrackMetadata(title="Known", duration_seconds=0)
    provenance = MetadataProvenance(title=DETAIL, duration_seconds=DETAIL)
    later = replace(DETAIL, observed_at=TIME + timedelta(minutes=1))
    assert original.merge_observation(TrackMetadata(), provenance, later) == (
        original,
        provenance,
    )
    assert original.merge_observation(original, provenance, later) == (
        original,
        MetadataProvenance(title=later, duration_seconds=later),
    )
    assert (
        original.merge_observation(
            TrackMetadata(duration_seconds=1), provenance, DISCOVERY
        )[0].duration_seconds
        == 0
    )


def test_unattributed_known_metadata_is_protected_from_search_but_can_be_refreshed() -> (
    None
):
    metadata = TrackMetadata(title="Imported")
    provenance = MetadataProvenance()
    assert metadata.merge_observation(
        TrackMetadata(title="Search"), provenance, DISCOVERY
    ) == (metadata, provenance)
    assert metadata.merge_observation(
        TrackMetadata(title="Confirmed"), provenance, DETAIL
    ) == (TrackMetadata(title="Confirmed"), MetadataProvenance(title=DETAIL))


def test_provenance_time_comparison_uses_instants_not_wall_clock_values() -> None:
    earlier = replace(
        DETAIL,
        observed_at=datetime(2026, 9, 21, 13, tzinfo=timezone(timedelta(hours=2))),
    )
    assert not earlier.supersedes(DETAIL)
    assert DETAIL.supersedes(earlier)
    equivalent = replace(
        DETAIL, observed_at=TIME.astimezone(timezone(timedelta(hours=2)))
    )
    assert not equivalent.supersedes(DETAIL)


@pytest.mark.parametrize("provider", ["", " ", " padded", "padded "])
def test_invalid_provider_provenance_is_rejected(provider: str) -> None:
    with pytest.raises(ValueError, match="provider"):
        replace(DETAIL, provider=provider)


def test_provenance_is_immutable_and_requires_a_typed_kind_and_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(DETAIL, observed_at=TIME.replace(tzinfo=None))
    with pytest.raises(ValueError, match="MetadataKind"):
        replace(DETAIL, kind=cast(MetadataKind, "detail"))
    for value, attribute in ((DETAIL, "provider"), (MetadataProvenance(), "title")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, None)

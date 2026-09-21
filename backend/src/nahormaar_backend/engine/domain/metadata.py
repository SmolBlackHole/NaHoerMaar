# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Shared track metadata and rules for combining partial observations."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite


class MetadataKind(StrEnum):
    DISCOVERY = "discovery"
    DETAIL = "detail"


@dataclass(frozen=True, slots=True)
class MetadataSource:
    """Where and when a successful provider observation was made."""

    provider: str
    kind: MetadataKind
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.provider or self.provider != self.provider.strip():
            raise ValueError("Metadata provider must be non-empty and trimmed.")
        if type(self.kind) is not MetadataKind:
            raise ValueError("Metadata source needs a MetadataKind.")
        if self.observed_at.utcoffset() is None:
            raise ValueError("Observation time must be timezone-aware.")

    def supersedes(self, previous: MetadataSource | None) -> bool:
        """Known but unattributed data needs detail to replace it, not discovery.

        Detail beats discovery. Equal-quality observations need a later timestamp;
        equal-time conflicts retain the accepted value instead of oscillating.
        Missing values are handled by the caller and can always be enriched.
        """
        if previous is None or self.kind != previous.kind:
            return self.kind is MetadataKind.DETAIL
        return self.observed_at.astimezone(UTC) > previous.observed_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MetadataProvenance:
    """Evidence for accepted values, not a second copy of their content."""

    title: MetadataSource | None = None
    artist: MetadataSource | None = None
    uploader: MetadataSource | None = None
    uploader_url: MetadataSource | None = None
    duration_seconds: MetadataSource | None = None
    thumbnail_url: MetadataSource | None = None
    artists: MetadataSource | None = None


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    """Known descriptive values; None means unknown, not a request to erase data."""

    title: str | None = None
    artist: str | None = None
    uploader: str | None = None
    uploader_url: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None

    def __post_init__(self) -> None:
        for attribute in fields(self):
            value = getattr(self, attribute.name)
            if isinstance(value, str) and (not value or value != value.strip()):
                raise ValueError(f"{attribute.name} must be non-empty and trimmed.")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("Duration must be finite and non-negative.")

    def merge(
        self, incoming: TrackMetadata, *, overwrite: bool = False
    ) -> TrackMetadata:
        """Fill gaps; callers may explicitly allow a refresh to replace known values.

        An overwrite is explicit, never implied by a partial observation.
        Missing values never erase known fields, even with overwrite enabled.
        """
        return replace(
            self,
            **{
                attribute.name: value
                for attribute in fields(self)
                if (value := getattr(incoming, attribute.name)) is not None
                and (overwrite or getattr(self, attribute.name) is None)
            },
        )

    def merge_observation(
        self,
        incoming: TrackMetadata,
        provenance: MetadataProvenance,
        source: MetadataSource,
    ) -> tuple[TrackMetadata, MetadataProvenance]:
        """Apply evidence per field so a partial lookup never downgrades details."""
        sources: dict[str, MetadataSource] = {}
        for attribute in fields(self):
            value = getattr(incoming, attribute.name)
            if value is not None and (
                getattr(self, attribute.name) is None
                or source.supersedes(getattr(provenance, attribute.name))
            ):
                sources[attribute.name] = source
        return (
            replace(self, **{name: getattr(incoming, name) for name in sources}),
            replace(provenance, **sources),
        )

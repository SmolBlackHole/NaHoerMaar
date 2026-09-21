# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Track and artist identities, timestamps and ordered credits."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .metadata import MetadataProvenance, MetadataSource, TrackMetadata


@dataclass(frozen=True, slots=True)
class MediaIdentity:
    """An opaque, case-sensitive ID in a provider's canonical media namespace.

    YouTube and YouTube Music adapters both use the youtube namespace for videos.
    URL parsing and namespace mapping belong to providers, not this value type.
    """

    namespace: str
    external_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("namespace", self.namespace),
            ("external_id", self.external_id),
        ):
            if not value or value != value.strip():
                raise ValueError(f"{name} must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class ArtistIdentity:
    """An artist's provider reference, never inferred from its display name."""

    namespace: str
    external_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("namespace", self.namespace),
            ("external_id", self.external_id),
        ):
            if not value or value != value.strip():
                raise ValueError(f"Artist {name} must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class Artist:
    """A known artist with a stable identity shared by its credited tracks."""

    identity: ArtistIdentity
    name: str
    id: UUID = field(default_factory=uuid4)
    name_source: MetadataSource | None = None

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("Artist name must be non-empty and trimmed.")


@dataclass(frozen=True, slots=True)
class Track:
    """A catalog record, not a queue entry or an individual playback occurrence.

    The caller supplies timestamps. A check can confirm unchanged metadata, or
    predate creation of the catalog row. It is not implied by a database write.
    Artist IDs preserve credit order; free-form artist text stays in metadata.
    """

    identity: MediaIdentity
    source_url: str
    metadata: TrackMetadata = field(default_factory=TrackMetadata)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(kw_only=True)
    updated_at: datetime = field(kw_only=True)
    checked_at: datetime | None = None
    artist_ids: tuple[UUID, ...] = ()
    provenance: MetadataProvenance = field(default_factory=MetadataProvenance)

    def __post_init__(self) -> None:
        if not self.source_url or self.source_url != self.source_url.strip():
            raise ValueError("source_url must be non-empty and trimmed.")
        for name, timestamp in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
            ("checked_at", self.checked_at),
        ):
            if timestamp is not None and timestamp.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if self.updated_at.astimezone(UTC) < self.created_at.astimezone(UTC):
            raise ValueError("Track update time cannot precede creation time.")
        if type(self.artist_ids) is not tuple:
            raise ValueError("Artist credits must be an immutable tuple of UUIDs.")
        if len(set(self.artist_ids)) != len(self.artist_ids):
            raise ValueError("A track cannot credit the same artist twice.")

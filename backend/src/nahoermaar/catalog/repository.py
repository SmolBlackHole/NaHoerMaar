# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Relational persistence for canonical catalog and discovery snapshots."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Select,
    Text,
    UniqueConstraint,
    Uuid,
    delete,
    exists,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from nahoermaar.database.schema import Base

from .domain import (
    Artist,
    ArtistCredit,
    ArtistId,
    ArtistSource,
    DiscoveryEntry,
    DiscoveryKind,
    DiscoverySnapshot,
    DiscoverySnapshotId,
    ObservationQuality,
    ProviderName,
    SourceAvailability,
    Track,
    TrackId,
    TrackSource,
    TrackSourceId,
)
from .providers import ProviderArtist, ProviderTrack


def _enum_values[EnumValue: StrEnum](members: type[EnumValue]) -> list[str]:
    return [member.value for member in members]


_PROVIDER = SqlEnum(
    ProviderName,
    name="catalog_provider",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_QUALITY = SqlEnum(
    ObservationQuality,
    name="observation_quality",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_AVAILABILITY = SqlEnum(
    SourceAvailability,
    name="source_availability",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)
_DISCOVERY_KIND = SqlEnum(
    DiscoveryKind,
    name="discovery_kind",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=_enum_values,
)


class _ArtistRow(Base):
    __tablename__ = "artists"
    __table_args__ = (
        CheckConstraint(
            "name = btrim(name) AND char_length(name) > 0", name="name_valid"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sources: Mapped[list[_ArtistSourceRow]] = relationship(
        back_populates="artist", cascade="all, delete-orphan", lazy="raise"
    )


class _ArtistSourceRow(Base):
    __tablename__ = "artist_sources"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="uq_artist_sources_provider_external_id"
        ),
        CheckConstraint(
            "external_id = btrim(external_id) AND char_length(external_id) > 0",
            name="external_id_valid",
        ),
        CheckConstraint(
            "observed_name = btrim(observed_name) AND char_length(observed_name) > 0",
            name="observed_name_valid",
        ),
        CheckConstraint(
            "checked_at >= first_seen_at", name="check_not_before_discovery"
        ),
    )

    artist_id: Mapped[UUID] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[ProviderName] = mapped_column(_PROVIDER, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200))
    observed_name: Mapped[str] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    artist: Mapped[_ArtistRow] = relationship(back_populates="sources")


class _TrackRow(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("isrc", name="uq_tracks_isrc"),
        CheckConstraint(
            "title = btrim(title) AND char_length(title) > 0", name="title_valid"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0", name="duration_positive"
        ),
        CheckConstraint("updated_at >= created_at", name="update_not_before_creation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    album_title: Mapped[str | None] = mapped_column(String(500))
    release_date: Mapped[date | None] = mapped_column(Date)
    isrc: Mapped[str | None] = mapped_column(String(15), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sources: Mapped[list[_TrackSourceRow]] = relationship(
        back_populates="track", cascade="all, delete-orphan", lazy="raise"
    )
    artist_links: Mapped[list[_TrackArtistRow]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        order_by="_TrackArtistRow.position",
        lazy="raise",
    )


class _TrackSourceRow(Base):
    __tablename__ = "track_sources"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="uq_track_sources_provider_external_id"
        ),
        CheckConstraint(
            "external_id = btrim(external_id) AND char_length(external_id) > 0",
            name="external_id_valid",
        ),
        CheckConstraint(
            "observed_title = btrim(observed_title) AND char_length(observed_title) > 0",
            name="observed_title_valid",
        ),
        CheckConstraint(
            "observed_duration_seconds IS NULL OR observed_duration_seconds > 0",
            name="observed_duration_positive",
        ),
        CheckConstraint(
            "checked_at >= first_seen_at", name="check_not_before_discovery"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[ProviderName] = mapped_column(_PROVIDER)
    external_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(Text)
    observed_title: Mapped[str] = mapped_column(String(500))
    observed_artist: Mapped[str | None] = mapped_column(String(500))
    observed_duration_seconds: Mapped[float | None] = mapped_column(Float)
    observed_artwork_url: Mapped[str | None] = mapped_column(Text)
    observed_album_title: Mapped[str | None] = mapped_column(String(500))
    observed_release_date: Mapped[date | None] = mapped_column(Date)
    observed_isrc: Mapped[str | None] = mapped_column(String(15))
    uploader_name: Mapped[str | None] = mapped_column(String(200))
    uploader_url: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[ObservationQuality] = mapped_column(_QUALITY)
    availability: Mapped[SourceAvailability] = mapped_column(_AVAILABILITY)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    track: Mapped[_TrackRow] = relationship(back_populates="sources")
    artist_links: Mapped[list[_TrackSourceArtistRow]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="_TrackSourceArtistRow.position",
        lazy="raise",
    )


class _TrackSourceArtistRow(Base):
    __tablename__ = "track_source_artists"
    __table_args__ = (
        UniqueConstraint(
            "track_source_id", "artist_id", name="uq_track_source_artists_source_artist"
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    track_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_sources.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist_id: Mapped[UUID] = mapped_column(
        ForeignKey("artists.id", ondelete="RESTRICT"), index=True
    )
    source: Mapped[_TrackSourceRow] = relationship(back_populates="artist_links")


class _TrackArtistRow(Base):
    __tablename__ = "track_artists"
    __table_args__ = (
        UniqueConstraint("track_id", "artist_id", name="uq_track_artists_track_artist"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist_id: Mapped[UUID] = mapped_column(
        ForeignKey("artists.id", ondelete="RESTRICT"), index=True
    )
    track: Mapped[_TrackRow] = relationship(back_populates="artist_links")
    artist: Mapped[_ArtistRow] = relationship(lazy="raise")


class _DiscoveryKeyRow(Base):
    __tablename__ = "discovery_keys"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "provider_key",
            "locator",
            "result_limit",
            name="uq_discovery_keys_identity",
        ),
        CheckConstraint(
            "locator = btrim(locator) AND char_length(locator) > 0",
            name="locator_valid",
        ),
        CheckConstraint("result_limit BETWEEN 1 AND 100", name="limit_valid"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[DiscoveryKind] = mapped_column(_DISCOVERY_KIND)
    provider_key: Mapped[str] = mapped_column(String(64))
    locator: Mapped[str] = mapped_column(String(500))
    result_limit: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )


class _DiscoverySnapshotRow(Base):
    __tablename__ = "discovery_snapshots"
    __table_args__ = (
        CheckConstraint("expires_at > fetched_at", name="positive_lifetime"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    key_id: Mapped[UUID] = mapped_column(
        ForeignKey("discovery_keys.id", ondelete="CASCADE"), index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    playlist_title: Mapped[str | None] = mapped_column(String(500))
    source_has_more: Mapped[bool] = mapped_column(Boolean)
    continuation: Mapped[str | None] = mapped_column(String(4096))


class _DiscoveryResultRow(Base):
    __tablename__ = "discovery_results"
    __table_args__ = (CheckConstraint("position >= 0", name="position_non_negative"),)

    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("discovery_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("track_sources.id", ondelete="RESTRICT"), index=True
    )


class CatalogConflictError(RuntimeError):
    """A provider identity was explicitly assigned to another canonical track."""


class CatalogRepository:
    """Persist canonical catalog aggregates within an existing transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, track_id: TrackId) -> Track | None:
        row = await self._session.scalar(
            _track_select().where(_TrackRow.id == track_id)
        )
        return _to_track(row) if row is not None else None

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]:
        if not track_ids:
            return {}
        rows = await self._session.scalars(
            _track_select().where(_TrackRow.id.in_(track_ids))
        )
        return {TrackId(row.id): _to_track(row) for row in rows.unique()}

    async def source(self, source_id: TrackSourceId) -> TrackSource | None:
        row = await self._session.get(_TrackSourceRow, source_id)
        return _to_source(row) if row is not None else None

    async def by_source(self, provider: ProviderName, external_id: str) -> Track | None:
        row = await self._session.scalar(
            _track_select()
            .join(_TrackSourceRow, _TrackSourceRow.track_id == _TrackRow.id)
            .where(
                _TrackSourceRow.provider == provider,
                _TrackSourceRow.external_id == external_id,
            )
        )
        return _to_track(row) if row is not None else None

    async def upsert(
        self,
        observation: ProviderTrack,
        observed_at: datetime,
        *,
        track_id: TrackId | None = None,
    ) -> Track:
        source = await self._session.scalar(
            select(_TrackSourceRow)
            .where(
                _TrackSourceRow.provider == observation.provider,
                _TrackSourceRow.external_id == observation.external_id,
            )
            .with_for_update()
        )
        is_new = source is None
        if source is not None:
            if track_id is not None and source.track_id != track_id:
                raise CatalogConflictError(
                    "Provider source already belongs to another track."
                )
            target_id = TrackId(source.track_id)
        else:
            target_id = track_id or await self._matching_track(observation)
            track = await self._session.get(_TrackRow, target_id)
            if track is None:
                self._session.add(
                    _TrackRow(
                        id=target_id,
                        title=observation.title,
                        duration_seconds=observation.duration_seconds,
                        artwork_url=observation.artwork_url,
                        album_title=observation.album_title,
                        release_date=observation.release_date,
                        isrc=observation.isrc,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                )
            source = _TrackSourceRow(
                id=uuid4(),
                track_id=target_id,
                provider=observation.provider,
                external_id=observation.external_id,
                source_url=observation.source_url,
                observed_title=observation.title,
                observed_artist=observation.artist_text,
                observed_duration_seconds=observation.duration_seconds,
                observed_artwork_url=observation.artwork_url,
                observed_album_title=observation.album_title,
                observed_release_date=observation.release_date,
                observed_isrc=observation.isrc,
                uploader_name=observation.uploader_name,
                uploader_url=observation.uploader_url,
                quality=observation.quality,
                availability=observation.availability,
                first_seen_at=observed_at,
                checked_at=observed_at,
            )
            self._session.add(source)
            await self._session.flush()

        replace_metadata = (
            is_new
            or observation.quality.priority > source.quality.priority
            or (
                observation.quality is source.quality
                and observed_at >= source.checked_at
            )
        )
        if observed_at >= source.checked_at:
            source.checked_at = observed_at
            source.availability = observation.availability
        if replace_metadata:
            source.source_url = observation.source_url
            source.observed_title = observation.title
            source.observed_artist = observation.artist_text or source.observed_artist
            source.observed_duration_seconds = (
                observation.duration_seconds or source.observed_duration_seconds
            )
            source.observed_artwork_url = (
                observation.artwork_url or source.observed_artwork_url
            )
            source.observed_album_title = (
                observation.album_title or source.observed_album_title
            )
            source.observed_release_date = (
                observation.release_date or source.observed_release_date
            )
            source.observed_isrc = observation.isrc or source.observed_isrc
            source.uploader_name = observation.uploader_name or source.uploader_name
            source.uploader_url = observation.uploader_url or source.uploader_url
            source.quality = observation.quality
            if observation.artists:
                await self._replace_source_artists(
                    source.id, observation.artists, observed_at
                )

        await self._session.flush()
        await self._recompute_track(target_id, observed_at)
        await self._session.flush()
        stored = await self.get(target_id)
        if stored is None:
            raise RuntimeError("Catalog upsert did not produce a track.")
        return stored

    async def mark_unavailable(
        self, provider: ProviderName, external_id: str, checked_at: datetime
    ) -> bool:
        source = await self._session.scalar(
            select(_TrackSourceRow)
            .where(
                _TrackSourceRow.provider == provider,
                _TrackSourceRow.external_id == external_id,
            )
            .with_for_update()
        )
        if source is None or checked_at < source.checked_at:
            return False
        source.availability = SourceAvailability.UNAVAILABLE
        source.checked_at = checked_at
        return True

    async def prune_orphans(self, checked_before: datetime) -> tuple[int, int, int]:
        source_values = tuple(
            await self._session.scalars(
                select(_TrackSourceRow.id).where(
                    _TrackSourceRow.checked_at < checked_before,
                    ~exists().where(
                        _DiscoveryResultRow.track_source_id == _TrackSourceRow.id
                    ),
                )
            )
        )
        if source_values:
            await self._session.execute(
                delete(_TrackSourceRow).where(_TrackSourceRow.id.in_(source_values))
            )
        track_values = tuple(
            await self._session.scalars(
                select(_TrackRow.id).where(
                    ~exists().where(_TrackSourceRow.track_id == _TrackRow.id)
                )
            )
        )
        if track_values:
            await self._session.execute(
                delete(_TrackRow).where(_TrackRow.id.in_(track_values))
            )
        artist_values = tuple(
            await self._session.scalars(
                select(_ArtistRow.id).where(
                    ~exists().where(_TrackArtistRow.artist_id == _ArtistRow.id),
                    ~exists().where(_TrackSourceArtistRow.artist_id == _ArtistRow.id),
                )
            )
        )
        if artist_values:
            await self._session.execute(
                delete(_ArtistRow).where(_ArtistRow.id.in_(artist_values))
            )
        return len(source_values), len(track_values), len(artist_values)

    async def _matching_track(self, observation: ProviderTrack) -> TrackId:
        if observation.isrc is not None:
            known = await self._session.scalar(
                select(_TrackRow.id).where(_TrackRow.isrc == observation.isrc)
            )
            if known is not None:
                return TrackId(known)
        return TrackId(uuid4())

    async def _replace_source_artists(
        self,
        source_id: UUID,
        observations: tuple[ProviderArtist, ...],
        observed_at: datetime,
    ) -> None:
        await self._session.execute(
            delete(_TrackSourceArtistRow).where(
                _TrackSourceArtistRow.track_source_id == source_id
            )
        )
        for position, observation in enumerate(observations):
            artist_id = await self._upsert_artist(observation, observed_at)
            self._session.add(
                _TrackSourceArtistRow(
                    track_source_id=source_id, position=position, artist_id=artist_id
                )
            )

    async def _upsert_artist(
        self, observation: ProviderArtist, observed_at: datetime
    ) -> ArtistId:
        source = await self._session.scalar(
            select(_ArtistSourceRow)
            .where(
                _ArtistSourceRow.provider == observation.provider,
                _ArtistSourceRow.external_id == observation.external_id,
            )
            .with_for_update()
        )
        if source is not None:
            if observed_at >= source.checked_at:
                source.observed_name = observation.name
                source.checked_at = observed_at
                artist = await self._session.get(_ArtistRow, source.artist_id)
                if artist is not None:
                    artist.name = observation.name
                    artist.updated_at = observed_at
            return ArtistId(source.artist_id)
        artist_id = ArtistId(uuid4())
        artist = _ArtistRow(
            id=artist_id,
            name=observation.name,
            created_at=observed_at,
            updated_at=observed_at,
        )
        artist.sources.append(
            _ArtistSourceRow(
                artist_id=artist_id,
                provider=observation.provider,
                external_id=observation.external_id,
                observed_name=observation.name,
                first_seen_at=observed_at,
                checked_at=observed_at,
            )
        )
        self._session.add(artist)
        await self._session.flush()
        return artist_id

    async def _recompute_track(self, track_id: TrackId, observed_at: datetime) -> None:
        track = await self._session.get(_TrackRow, track_id)
        if track is None:
            raise RuntimeError("Cannot recompute an unknown track.")
        sources = tuple(
            await self._session.scalars(
                select(_TrackSourceRow).where(_TrackSourceRow.track_id == track_id)
            )
        )
        ranked = sorted(
            sources,
            key=lambda source: (
                source.quality.priority,
                source.checked_at,
                source.provider.value,
                source.external_id,
            ),
            reverse=True,
        )

        def preferred(attribute: str) -> object | None:
            return next(
                (
                    value
                    for source in ranked
                    if (value := getattr(source, attribute)) is not None
                ),
                None,
            )

        title = preferred("observed_title")
        if not isinstance(title, str):
            raise RuntimeError("A canonical track requires a title.")
        track.title = title
        duration = preferred("observed_duration_seconds")
        artwork = preferred("observed_artwork_url")
        album = preferred("observed_album_title")
        released = preferred("observed_release_date")
        isrc = preferred("observed_isrc")
        track.duration_seconds = duration if isinstance(duration, float) else None
        track.artwork_url = artwork if isinstance(artwork, str) else None
        track.album_title = album if isinstance(album, str) else None
        track.release_date = released if isinstance(released, date) else None
        track.isrc = isrc if isinstance(isrc, str) else None
        track.updated_at = max(track.updated_at, observed_at)

        selected_links: tuple[_TrackSourceArtistRow, ...] = ()
        for source in ranked:
            links = tuple(
                await self._session.scalars(
                    select(_TrackSourceArtistRow)
                    .where(_TrackSourceArtistRow.track_source_id == source.id)
                    .order_by(_TrackSourceArtistRow.position)
                )
            )
            if links:
                selected_links = links
                break
        await self._session.execute(
            delete(_TrackArtistRow).where(_TrackArtistRow.track_id == track_id)
        )
        self._session.add_all(
            _TrackArtistRow(
                track_id=track_id, position=link.position, artist_id=link.artist_id
            )
            for link in selected_links
        )


class DiscoveryRepository:
    """Persist immutable ordered discovery snapshots."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, snapshot_id: DiscoverySnapshotId) -> DiscoverySnapshot | None:
        snapshot = await self._session.get(_DiscoverySnapshotRow, snapshot_id)
        if snapshot is None:
            return None
        key = await self._session.get(_DiscoveryKeyRow, snapshot.key_id)
        if key is None:
            raise RuntimeError("Discovery snapshot has no key.")
        return await self._snapshot(key, snapshot)

    async def latest(
        self,
        kind: DiscoveryKind,
        provider_key: str,
        locator: str,
        limit: int,
        *,
        requested_at: datetime | None = None,
    ) -> DiscoverySnapshot | None:
        key = await self._session.scalar(
            select(_DiscoveryKeyRow).where(
                _DiscoveryKeyRow.kind == kind,
                _DiscoveryKeyRow.provider_key == provider_key,
                _DiscoveryKeyRow.locator == locator,
                _DiscoveryKeyRow.result_limit == limit,
            )
        )
        if key is None:
            return None
        if requested_at is not None and requested_at > key.last_requested_at:
            key.last_requested_at = requested_at
        snapshot = await self._session.scalar(
            select(_DiscoverySnapshotRow)
            .where(_DiscoverySnapshotRow.key_id == key.id)
            .order_by(
                _DiscoverySnapshotRow.fetched_at.desc(), _DiscoverySnapshotRow.id.desc()
            )
            .limit(1)
        )
        return await self._snapshot(key, snapshot) if snapshot is not None else None

    async def publish(
        self,
        *,
        kind: DiscoveryKind,
        provider_key: str,
        locator: str,
        limit: int,
        source_url: str | None,
        playlist_title: str | None,
        source_ids: tuple[TrackSourceId, ...],
        fetched_at: datetime,
        expires_at: datetime,
        source_has_more: bool,
        continuation: str | None,
        keep: int = 3,
    ) -> DiscoverySnapshot:
        key_id = await self._session.scalar(
            insert(_DiscoveryKeyRow)
            .values(
                id=uuid4(),
                kind=kind,
                provider_key=provider_key,
                locator=locator,
                result_limit=limit,
                source_url=source_url,
                created_at=fetched_at,
                last_requested_at=fetched_at,
            )
            .on_conflict_do_update(
                constraint="uq_discovery_keys_identity",
                set_={"source_url": source_url, "last_requested_at": fetched_at},
            )
            .returning(_DiscoveryKeyRow.id)
        )
        if key_id is None:
            raise RuntimeError("Discovery key upsert returned no identity.")
        snapshot_id = DiscoverySnapshotId(uuid4())
        snapshot_row = _DiscoverySnapshotRow(
            id=snapshot_id,
            key_id=key_id,
            fetched_at=fetched_at,
            expires_at=expires_at,
            playlist_title=playlist_title,
            source_has_more=source_has_more,
            continuation=continuation,
        )
        self._session.add(snapshot_row)
        await self._session.flush()
        self._session.add_all(
            _DiscoveryResultRow(
                snapshot_id=snapshot_id, position=position, track_source_id=source_id
            )
            for position, source_id in enumerate(source_ids)
        )
        await self._session.flush()
        stale = tuple(
            await self._session.scalars(
                select(_DiscoverySnapshotRow.id)
                .where(_DiscoverySnapshotRow.key_id == key_id)
                .order_by(
                    _DiscoverySnapshotRow.fetched_at.desc(),
                    _DiscoverySnapshotRow.id.desc(),
                )
                .offset(keep)
            )
        )
        if stale:
            await self._session.execute(
                delete(_DiscoverySnapshotRow).where(_DiscoverySnapshotRow.id.in_(stale))
            )
        key = await self._session.get(_DiscoveryKeyRow, key_id)
        snapshot = await self._session.get(_DiscoverySnapshotRow, snapshot_id)
        if key is None or snapshot is None:
            raise RuntimeError("Published discovery snapshot disappeared.")
        return await self._snapshot(key, snapshot)

    async def _snapshot(
        self, key: _DiscoveryKeyRow, snapshot: _DiscoverySnapshotRow
    ) -> DiscoverySnapshot:
        results = tuple(
            await self._session.scalars(
                select(_DiscoveryResultRow)
                .where(_DiscoveryResultRow.snapshot_id == snapshot.id)
                .order_by(_DiscoveryResultRow.position)
            )
        )
        source_ids = {result.track_source_id for result in results}
        sources = (
            tuple(
                await self._session.scalars(
                    select(_TrackSourceRow).where(_TrackSourceRow.id.in_(source_ids))
                )
            )
            if source_ids
            else ()
        )
        source_map = {source.id: source for source in sources}
        tracks = await CatalogRepository(self._session).tracks(
            {TrackId(source.track_id) for source in sources}
        )
        entries = tuple(
            DiscoveryEntry(
                result.position,
                tracks[TrackId(source_map[result.track_source_id].track_id)],
                _to_source(source_map[result.track_source_id]),
            )
            for result in results
        )
        return DiscoverySnapshot(
            DiscoverySnapshotId(snapshot.id),
            key.kind,
            key.provider_key,
            key.locator,
            key.result_limit,
            snapshot.fetched_at,
            snapshot.expires_at,
            entries,
            key.source_url,
            snapshot.playlist_title,
            snapshot.source_has_more,
            snapshot.continuation,
        )


def _track_select() -> Select[tuple[_TrackRow]]:
    return select(_TrackRow).options(
        selectinload(_TrackRow.sources),
        selectinload(_TrackRow.artist_links)
        .selectinload(_TrackArtistRow.artist)
        .selectinload(_ArtistRow.sources),
    )


def _to_source(row: _TrackSourceRow) -> TrackSource:
    return TrackSource(
        TrackSourceId(row.id),
        TrackId(row.track_id),
        row.provider,
        row.external_id,
        row.source_url,
        row.observed_title,
        row.observed_artist,
        row.observed_duration_seconds,
        row.observed_artwork_url,
        row.observed_album_title,
        row.observed_release_date,
        row.observed_isrc,
        row.uploader_name,
        row.uploader_url,
        row.quality,
        row.availability,
        row.first_seen_at,
        row.checked_at,
    )


def _to_artist(row: _ArtistRow) -> Artist:
    return Artist(
        ArtistId(row.id),
        row.name,
        row.created_at,
        row.updated_at,
        tuple(
            ArtistSource(
                source.provider,
                source.external_id,
                source.observed_name,
                source.first_seen_at,
                source.checked_at,
            )
            for source in sorted(
                row.sources, key=lambda value: (value.provider.value, value.external_id)
            )
        ),
    )


def _to_track(row: _TrackRow) -> Track:
    return Track(
        TrackId(row.id),
        row.title,
        row.duration_seconds,
        row.artwork_url,
        row.album_title,
        row.release_date,
        row.isrc,
        row.created_at,
        row.updated_at,
        tuple(
            ArtistCredit(_to_artist(link.artist), link.position)
            for link in sorted(row.artist_links, key=lambda value: value.position)
        ),
        tuple(
            _to_source(source)
            for source in sorted(
                row.sources, key=lambda value: (value.provider.value, value.external_id)
            )
        ),
    )

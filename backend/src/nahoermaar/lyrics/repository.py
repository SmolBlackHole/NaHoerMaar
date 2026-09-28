# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Relational cache for lyrics resolved for canonical tracks."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    String,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from nahoermaar.catalog.domain import TrackId
from nahoermaar.database.schema import Base, enum_values

from .domain import LyricsState, TrackLyrics


_STATE = SqlEnum(
    LyricsState,
    name="lyrics_state",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=enum_values,
)


class _TrackLyricsRow(Base):
    __tablename__ = "track_lyrics"
    __table_args__ = (
        CheckConstraint(
            "char_length(metadata_signature) = 64",
            name="metadata_signature_sha256",
        ),
        CheckConstraint("expires_at > fetched_at", name="expiry_after_fetch"),
        CheckConstraint(
            "("
            "(state = 'available' AND provider_record_id IS NOT NULL "
            "AND (plain_lyrics IS NOT NULL OR synced_lyrics IS NOT NULL)) OR "
            "(state = 'instrumental' AND provider_record_id IS NOT NULL "
            "AND plain_lyrics IS NULL AND synced_lyrics IS NULL) OR "
            "(state = 'not_found' AND provider_record_id IS NULL "
            "AND plain_lyrics IS NULL AND synced_lyrics IS NULL)"
            ")",
            name="payload_matches_state",
        ),
    )

    track_id: Mapped[UUID] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metadata_signature: Mapped[str] = mapped_column(String(64))
    state: Mapped[LyricsState] = mapped_column(_STATE)
    provider_record_id: Mapped[int | None] = mapped_column(BigInteger)
    plain_lyrics: Mapped[str | None] = mapped_column(Text)
    synced_lyrics: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LyricsRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, track_id: TrackId) -> TrackLyrics | None:
        row = await self._session.scalar(
            select(_TrackLyricsRow).where(_TrackLyricsRow.track_id == track_id)
        )
        return _to_domain(row) if row is not None else None

    async def save(self, lyrics: TrackLyrics) -> None:
        row = await self._session.get(_TrackLyricsRow, lyrics.track_id)
        if row is None:
            row = _TrackLyricsRow(track_id=lyrics.track_id)
            self._session.add(row)
        row.metadata_signature = lyrics.metadata_signature
        row.state = lyrics.state
        row.provider_record_id = lyrics.provider_record_id
        row.plain_lyrics = lyrics.plain_lyrics
        row.synced_lyrics = lyrics.synced_lyrics
        row.fetched_at = lyrics.fetched_at
        row.expires_at = lyrics.expires_at
        await self._session.flush()


def _to_domain(row: _TrackLyricsRow) -> TrackLyrics:
    return TrackLyrics(
        TrackId(row.track_id),
        row.metadata_signature,
        row.state,
        row.provider_record_id,
        row.plain_lyrics,
        row.synced_lyrics,
        row.fetched_at,
        row.expires_at,
    )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated catalog, search and playlist queries."""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from nahoermaar.catalog.domain import (
    DiscoveryResult,
    DiscoverySnapshotId,
    Track,
    TrackSource,
)
from nahoermaar.catalog.service import CatalogService

from .errors import error_responses
from .pagination import OffsetPageView


class View(BaseModel):
    model_config = ConfigDict(frozen=True)


class ArtistView(View):
    id: UUID
    name: str


class TrackSourceView(View):
    id: UUID
    provider: str
    external_id: str
    source_url: str
    availability: str
    checked_at: datetime


class TrackView(View):
    id: UUID
    title: str
    artists: tuple[ArtistView, ...]
    duration_seconds: float | None
    artwork_url: str | None
    album_title: str | None
    release_date: date | None
    isrc: str | None
    sources: tuple[TrackSourceView, ...]


class DiscoveryEntryView(View):
    position: int
    track: TrackView
    source: TrackSourceView


class DiscoveryView(OffsetPageView[DiscoveryEntryView]):
    version: UUID
    kind: str
    provider: str
    query: str | None
    source_url: str | None
    playlist_title: str | None
    fetched_at: datetime
    expires_at: datetime
    stale: bool
    refreshing: bool
    source_has_more: bool


def router(catalog: CatalogService) -> APIRouter:
    api = APIRouter(prefix="/api/catalog", tags=["catalog"])

    @api.get(
        "/search",
        response_model=DiscoveryView,
        operation_id="searchCatalog",
        responses=error_responses(401, 422, 500, 502, 503),
    )
    async def search(
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        provider: str = Query(default="youtube_music", min_length=1, max_length=64),
        refresh: bool = False,
    ) -> DiscoveryView:
        return _discovery(
            await catalog.search(
                q, limit=limit, provider_key=provider, refresh=refresh
            ),
            page_size=min(20, limit),
        )

    @api.get(
        "/playlist",
        response_model=DiscoveryView,
        operation_id="discoverCatalogPlaylist",
        responses=error_responses(401, 422, 500, 502, 503),
    )
    async def playlist(
        url: str = Query(min_length=1, max_length=2048),
        limit: int = Query(default=100, ge=1, le=100),
        provider: str | None = Query(default=None, min_length=1, max_length=64),
        refresh: bool = False,
    ) -> DiscoveryView:
        return _discovery(
            await catalog.playlist(
                url,
                limit=limit,
                provider_key=provider,
                refresh=refresh,
            )
        )

    @api.get(
        "/link",
        response_model=TrackView,
        operation_id="resolveCatalogLink",
        responses=error_responses(401, 404, 422, 500, 502, 503),
    )
    async def link(
        url: str = Query(min_length=1, max_length=2048),
        provider: str | None = Query(default=None, min_length=1, max_length=64),
    ) -> TrackView:
        return track_view(await catalog.track(url, provider_key=provider))

    @api.get(
        "/discoveries/{version}",
        response_model=DiscoveryView,
        operation_id="getCatalogDiscovery",
        responses=error_responses(401, 404, 422, 500, 503),
    )
    async def snapshot(
        version: UUID,
        offset: int = Query(default=0, ge=0),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> DiscoveryView:
        return _discovery(
            await catalog.snapshot(DiscoverySnapshotId(version)),
            offset=offset,
            page_size=page_size,
        )

    @api.post(
        "/discoveries/{version}/continuations",
        response_model=DiscoveryView,
        operation_id="continueCatalogDiscovery",
        responses=error_responses(401, 404, 422, 500, 502, 503),
    )
    async def continue_snapshot(
        version: UUID,
        offset: int = Query(ge=0),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> DiscoveryView:
        return _discovery(
            await catalog.continue_snapshot(
                DiscoverySnapshotId(version),
                limit=page_size,
            ),
            offset=offset,
            page_size=page_size,
        )

    return api


def source_view(source: TrackSource) -> TrackSourceView:
    return TrackSourceView(
        id=source.id,
        provider=source.provider.value,
        external_id=source.external_id,
        source_url=source.source_url,
        availability=source.availability.value,
        checked_at=source.checked_at,
    )


def track_view(track: Track) -> TrackView:
    return TrackView(
        id=track.id,
        title=track.title,
        artists=tuple(
            ArtistView(id=credit.artist.id, name=credit.artist.name)
            for credit in track.artists
        ),
        duration_seconds=track.duration_seconds,
        artwork_url=track.artwork_url,
        album_title=track.album_title,
        release_date=track.release_date,
        isrc=track.isrc,
        sources=tuple(source_view(source) for source in track.sources),
    )


def _discovery(
    result: DiscoveryResult,
    *,
    offset: int = 0,
    page_size: int | None = None,
) -> DiscoveryView:
    snapshot = result.snapshot
    total = len(snapshot.entries)
    end = None if page_size is None else offset + page_size
    entries = snapshot.entries[offset:end]
    next_offset = offset + len(entries)
    return DiscoveryView(
        version=snapshot.id,
        kind=snapshot.kind.value,
        provider=snapshot.provider_key,
        query=snapshot.locator if snapshot.kind.value == "search" else None,
        source_url=snapshot.source_url,
        playlist_title=snapshot.playlist_title,
        fetched_at=snapshot.fetched_at,
        expires_at=snapshot.expires_at,
        stale=result.stale,
        refreshing=result.refreshing,
        offset=offset,
        page_size=len(entries),
        total=total,
        next_offset=next_offset if next_offset < total else None,
        source_has_more=snapshot.source_has_more,
        items=tuple(
            DiscoveryEntryView(
                position=entry.position,
                track=track_view(entry.track),
                source=source_view(entry.source),
            )
            for entry in entries
        ),
    )

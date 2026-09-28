# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Authenticated lyrics query for canonical Catalog tracks."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from nahoermaar.catalog.domain import TrackId
from nahoermaar.lyrics.domain import LyricLine, LyricsResult, LyricsState
from nahoermaar.lyrics.service import LyricsService

from .errors import error_responses


class View(BaseModel):
    model_config = ConfigDict(frozen=True)


class LyricLineView(View):
    text: str
    start_seconds: float | None
    end_seconds: float | None


class LyricsView(View):
    track_id: UUID
    state: LyricsState
    synchronized: bool
    lines: tuple[LyricLineView, ...]
    provider: str
    provider_url: str
    fetched_at: datetime
    expires_at: datetime
    cached: bool
    stale: bool


def router(lyrics: LyricsService) -> APIRouter:
    routes = APIRouter(prefix="/api/tracks", tags=["lyrics"])

    @routes.get(
        "/{track_id}/lyrics",
        response_model=LyricsView,
        operation_id="getTrackLyrics",
        responses=error_responses(401, 404, 422, 500, 502),
    )
    async def get_lyrics(track_id: UUID, refresh: bool = False) -> LyricsView:
        return lyrics_view(await lyrics.get(TrackId(track_id), refresh=refresh))

    return routes


def lyrics_view(result: LyricsResult) -> LyricsView:
    lyrics = result.lyrics
    lines = lyrics.lines(None)
    return LyricsView(
        track_id=lyrics.track_id,
        state=lyrics.state,
        synchronized=bool(
            lyrics.synced_lyrics
            and any(line.start_seconds is not None for line in lines)
        ),
        lines=tuple(_line_view(line) for line in lines),
        provider="LRCLIB",
        provider_url=(
            f"https://lrclib.net/api/get/{lyrics.provider_record_id}"
            if lyrics.provider_record_id is not None
            else "https://lrclib.net"
        ),
        fetched_at=lyrics.fetched_at,
        expires_at=lyrics.expires_at,
        cached=result.cached,
        stale=result.stale,
    )


def _line_view(line: LyricLine) -> LyricLineView:
    return LyricLineView(
        text=line.text,
        start_seconds=line.start_seconds,
        end_seconds=line.end_seconds,
    )

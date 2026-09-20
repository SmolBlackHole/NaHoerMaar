# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Search results and account-owned playlist previews."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ...application.catalog import MediaCatalog, PlaylistPreview
from ...domain.catalog import SearchPage, SearchSource
from .. import schemas as dto
from ..dependencies import ApiServices, CurrentUser, RequestID


def discovery_router(services: ApiServices) -> APIRouter:
    router = APIRouter()

    @router.get("/api/catalog/search")
    async def search(
        q: Annotated[str, Query(min_length=1, max_length=200)],
        library: Annotated[MediaCatalog, Depends(services.catalog)],
        offset: Annotated[int, Query(ge=0, le=90, multiple_of=10)] = 0,
        source: SearchSource = SearchSource.MUSIC,
        snapshot_id: Annotated[str | None, Query(min_length=32, max_length=32)] = None,
    ) -> SearchPage:
        if not q.strip():
            raise HTTPException(422, detail="Enter a title or artist.")
        try:
            return await library.search(q, offset, source, snapshot_id)
        except KeyError as exc:
            raise HTTPException(
                410, detail="These results expired. Refresh the search."
            ) from exc

    @router.post("/api/youtube/playlists", status_code=202)
    async def preview_playlist(
        user: CurrentUser,
        body: dto.PlaylistInput,
        request_id: RequestID,
        library: Annotated[MediaCatalog, Depends(services.catalog)],
    ) -> PlaylistPreview:
        try:
            return library.start_preview(
                body.source_url.strip(), request_id, owner_id=user.account.profile.id
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @router.get("/api/youtube/playlists/{preview_id}")
    async def playlist_preview(
        preview_id: UUID,
        library: Annotated[MediaCatalog, Depends(services.catalog)],
        user: CurrentUser,
    ) -> PlaylistPreview:
        try:
            return library.preview(preview_id, owner_id=user.account.profile.id)
        except KeyError as exc:
            raise HTTPException(
                410, detail="This playlist preview expired. Open the playlist again."
            ) from exc

    @router.delete("/api/youtube/playlists/{preview_id}")
    async def cancel_playlist(
        preview_id: UUID,
        library: Annotated[MediaCatalog, Depends(services.catalog)],
        user: CurrentUser,
    ) -> PlaylistPreview:
        try:
            return await library.cancel_preview(
                preview_id, owner_id=user.account.profile.id
            )
        except KeyError as exc:
            raise HTTPException(410, detail="This playlist preview expired.") from exc

    return router

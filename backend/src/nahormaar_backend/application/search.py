# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Search providers with one bounded, shared result cache."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from ..cache import CatalogBusy, SnapshotCache
from ..domain.catalog import CatalogTrack, SearchPage, SearchSource

SEARCH_LIMIT = 10
SEARCH_MAX_RESULTS = 100
SEARCH_CACHE_LIMIT = 32
SEARCH_CACHE_TTL = 300.0
SEARCH_PENDING_LIMIT = 8


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int) -> tuple[CatalogTrack, ...]: ...


class SearchCatalog:
    def __init__(
        self,
        providers: Mapping[SearchSource, SearchProvider],
        remember: Callable[[CatalogTrack], None] | None = None,
    ) -> None:
        self._providers = providers
        self._remember = remember
        self._cache = SnapshotCache[tuple[CatalogTrack, ...]](
            SEARCH_CACHE_LIMIT, SEARCH_CACHE_TTL, SEARCH_PENDING_LIMIT
        )

    async def search(
        self,
        query: str,
        offset: int = 0,
        source: SearchSource = SearchSource.MUSIC,
        snapshot_id: str | None = None,
    ) -> SearchPage:
        query = " ".join(query.split())
        if not 1 <= len(query) <= 200:
            raise ValueError("Search must contain 1 to 200 characters.")
        if offset < 0 or offset >= SEARCH_MAX_RESULTS or offset % SEARCH_LIMIT:
            raise ValueError("Invalid search offset.")
        if self._cache.closed:
            raise CatalogBusy("Music discovery is closed.")
        key = f"{source}:{query.casefold()}"
        snapshot = (
            self._cache.version(key, snapshot_id)
            if snapshot_id
            else await self._cache.get(key, lambda: self._fetch(source, query))
        )
        latest = self._cache.peek(key) or snapshot
        end = offset + SEARCH_LIMIT
        return SearchPage(
            snapshot.value[offset:end],
            end if len(snapshot.value) > end else None,
            snapshot.id,
            latest.id,
            key in self._cache.pending,
            self._cache.errors.get(key),
        )

    async def _fetch(
        self, source: SearchSource, query: str
    ) -> tuple[CatalogTrack, ...]:
        tracks = (await self._providers[source].search(query, SEARCH_MAX_RESULTS))[
            :SEARCH_MAX_RESULTS
        ]
        if self._remember:
            for entry in tracks:
                self._remember(entry)
        return tracks

    async def close(self) -> None:
        await self._cache.close()

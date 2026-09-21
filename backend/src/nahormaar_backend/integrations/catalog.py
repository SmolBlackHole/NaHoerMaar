# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Compose the shared discovery catalog from concrete provider adapters."""

from pathlib import Path

from ..application.catalog import MediaCatalog
from ..domain.catalog import SearchSource
from .discovery import DiscoveryExtractor
from .youtube_search import YouTubeMusicSearch, YouTubeVideoSearch


async def create_media_catalog(node_path: Path) -> MediaCatalog:
    """Transfer the extractor to the catalog, or close it on startup failure."""
    extractor = DiscoveryExtractor(node_path)
    try:
        return MediaCatalog(
            extractor,
            {
                SearchSource.MUSIC: YouTubeMusicSearch(extractor.execute),
                SearchSource.VIDEOS: YouTubeVideoSearch(extractor.run),
            },
        )
    except BaseException:
        await extractor.close()
        raise

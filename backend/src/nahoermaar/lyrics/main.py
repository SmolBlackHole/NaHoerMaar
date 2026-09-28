# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Lyrics module composition."""

from dataclasses import dataclass

from nahoermaar.catalog.service import CatalogService
from nahoermaar.database.uow import UnitOfWorkFactory

from .providers import LyricsProvider
from .service import LyricsService


@dataclass(frozen=True, slots=True)
class LyricsModule:
    service: LyricsService


def create_lyrics_module(
    units: UnitOfWorkFactory,
    catalog: CatalogService,
    provider: LyricsProvider,
) -> LyricsModule:
    return LyricsModule(LyricsService(units, catalog, provider))

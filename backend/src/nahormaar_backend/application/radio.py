# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Bounded account-owned recommendation previews; no queue side effects."""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import UUID

from ..domain.catalog import CatalogTrack
from ..domain.models import TrackMetadata
from ..domain.radio import RadioSeed
from .audio import TrackError

RADIO_PREVIEW_LIMIT = 25
RADIO_POOL_LIMIT = 50
RADIO_BUFFER = 3
RADIO_PREVIEW_TTL = 600
RADIO_PREVIEWS = 32


class RecommendationProvider(Protocol):
    async def recommend(
        self, seed: RadioSeed, limit: int
    ) -> tuple[CatalogTrack, ...]: ...


@dataclass(frozen=True, slots=True)
class RadioPreview:
    id: UUID
    seed: RadioSeed
    entries: tuple[CatalogTrack, ...]


class RadioCatalog:
    def __init__(self, provider: RecommendationProvider) -> None:
        self.provider = provider
        self._previews: dict[UUID, tuple[UUID, float, RadioPreview]] = {}
        self._pending: dict[
            UUID, tuple[UUID, RadioSeed, asyncio.Task[RadioPreview]]
        ] = {}
        self._closed = False

    async def preview(
        self, identifier: UUID, owner: UUID, seed: RadioSeed
    ) -> RadioPreview:
        if self._closed:
            raise TrackError("Radio is unavailable.")
        self._previews = {
            key: value
            for key, value in self._previews.items()
            if value[1] > monotonic()
        }
        if identifier in self._previews:
            existing = self.get(identifier, owner)
            if existing.seed != seed:
                raise ValueError("This preview request was already used.")
            return existing
        pending = self._pending.get(identifier)
        if pending:
            if pending[:2] != (owner, seed):
                raise ValueError("This preview request was already used.")
            return await asyncio.shield(pending[2])
        if len(self._pending) >= 4:
            raise TrackError("Radio discovery is busy. Try again shortly.")

        async def fetch() -> RadioPreview:
            try:
                async with asyncio.timeout(35):
                    tracks = await self.provider.recommend(seed, RADIO_POOL_LIMIT)
                seen: set[str] = set()
                entries: list[CatalogTrack] = []
                for entry in tracks:
                    if (
                        entry.video_id
                        and entry.source_url
                        and not entry.unavailable
                        and entry.video_id not in seen
                    ):
                        seen.add(entry.video_id)
                        entries.append(entry)
                preview = RadioPreview(
                    identifier, seed, tuple(entries[:RADIO_PREVIEW_LIMIT])
                )
                if len(self._previews) >= RADIO_PREVIEWS:
                    self._previews.pop(next(iter(self._previews)))
                self._previews[identifier] = (
                    owner,
                    monotonic() + RADIO_PREVIEW_TTL,
                    preview,
                )
                return preview
            except TimeoutError:
                raise TrackError("Radio took too long. Try again.") from None
            finally:
                self._pending.pop(identifier, None)

        task = asyncio.create_task(fetch())
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )
        self._pending[identifier] = (owner, seed, task)
        return await asyncio.shield(task)

    def get(self, identifier: UUID, owner: UUID | None) -> RadioPreview:
        stored = self._previews.get(identifier)
        if stored is None or stored[0] != owner or stored[1] <= monotonic():
            raise ValueError("Open the radio preview again.")
        return stored[2]

    def metadata(self, video_id: str) -> TrackMetadata | None:
        for _, expiry, preview in reversed(tuple(self._previews.values())):
            if expiry > monotonic():
                for entry in preview.entries:
                    if entry.video_id == video_id:
                        return entry.metadata()
        return None

    async def close(self) -> None:
        self._closed = True
        tasks = [item[2] for item in self._pending.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._previews.clear()

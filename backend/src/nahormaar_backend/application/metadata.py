# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Merge public track metadata and resolve missing queue metadata asynchronously."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import fields, replace
from uuid import UUID

from ..domain.models import PlayerSnapshot, QueueEntry, TrackMetadata
from .audio import MetadataResolver, TrackError


def merge_metadata[T: (TrackMetadata, QueueEntry)](
    target: T, source: TrackMetadata | QueueEntry, *, overwrite: bool = True
) -> T:
    """Copy known metadata, preserving queue identity and attribution.

    Only None is missing. With overwrite=False, existing values take precedence.
    """
    return replace(
        target,
        **{
            field.name: value
            for field in fields(TrackMetadata)
            if (value := getattr(source, field.name)) is not None
            and (overwrite or getattr(target, field.name) is None)
        },
    )


class QueueMetadata:
    def __init__(
        self,
        resolver: MetadataResolver,
        *,
        snapshot: Callable[[], PlayerSnapshot],
        active: Callable[[], bool],
        needs_refresh: Callable[[str], bool],
        apply: Callable[[UUID, TrackMetadata], Awaitable[None]],
    ) -> None:
        self._resolver = resolver
        self._snapshot = snapshot
        self._active = active
        self._needs_refresh = needs_refresh
        self._apply = apply
        self._wake = asyncio.Event()
        self._wake.set()
        self._task = asyncio.create_task(self._run())

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        attempted: set[UUID] = set()
        while self._active():
            await self._wake.wait()
            self._wake.clear()
            upcoming = self._snapshot().upcoming
            attempted.intersection_update(entry.id for entry in upcoming)
            entry = next(
                (
                    entry
                    for entry in upcoming
                    if entry.id not in attempted
                    and (
                        entry.title is None
                        or entry.duration_seconds is None
                        or self._needs_refresh(entry.source_url)
                    )
                ),
                None,
            )
            if entry is None:
                continue
            attempted.add(entry.id)
            try:
                metadata = await self._resolver.metadata(entry.source_url)
            except TrackError:
                self._wake.set()
                continue
            except Exception:
                logging.getLogger(__name__).warning("Could not load queue metadata.")
                self._wake.set()
                continue
            try:
                await self._apply(entry.id, metadata)
            except RuntimeError:
                return
            self._wake.set()

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

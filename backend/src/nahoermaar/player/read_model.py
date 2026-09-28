# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Immutable Player-owned projection for HTTP responses and live events."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from nahoermaar.catalog.domain import (
    DiscoveryResult,
    DiscoverySnapshotId,
    Track,
    TrackId,
    TrackSourceId,
)
from nahoermaar.catalog.service import CatalogError
from nahoermaar.users.domain import User, UserId

from .domain import PlayerState, TrackRequest
from .playback import PlaybackRuntimeState


class CatalogReader(Protocol):
    """Catalog reads needed to enrich one Player state."""

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]: ...

    async def track_for_source(self, source_id: TrackSourceId) -> Track | None: ...

    async def snapshot(
        self,
        snapshot_id: DiscoverySnapshotId,
    ) -> DiscoveryResult: ...


class UserReader(Protocol):
    """User reads needed to identify request contributors."""

    async def users(self, user_ids: set[UserId]) -> dict[UserId, User]: ...


@dataclass(frozen=True, slots=True)
class PlayerReadModel:
    """One enriched Player state shared by every delivery mechanism."""

    state: PlayerState
    runtime: PlaybackRuntimeState
    current_request: TrackRequest | None
    tracks: Mapping[TrackId, Track]
    contributors: Mapping[UserId, User]
    radio_seed_track: Track | None
    radio_seed_title: str | None


class PlayerReader:
    """Build one side-effect-free projection from Player-owned state."""

    __slots__ = ("_catalog", "_runtime", "_users")

    def __init__(
        self,
        catalog: CatalogReader,
        users: UserReader,
        runtime: Callable[[], PlaybackRuntimeState],
    ) -> None:
        self._catalog = catalog
        self._users = users
        self._runtime = runtime

    async def read(self, state: PlayerState) -> PlayerReadModel:
        runtime = self._runtime()
        current_request = state.checkpoint.request or runtime.request
        track_ids = {entry.track_id for entry in state.queue.entries}
        if current_request is not None:
            track_ids.add(current_request.track_id)

        run = state.radio if state.radio is not None and state.radio.active else None
        radio_seed_track: Track | None = None
        radio_seed_title: str | None = None
        if run is not None and run.seed.track_source_id is not None:
            radio_seed_track = await self._catalog.track_for_source(
                run.seed.track_source_id
            )
            if radio_seed_track is not None:
                track_ids.add(radio_seed_track.id)
                radio_seed_title = radio_seed_track.title
        elif run is not None and run.seed.discovery_snapshot_id is not None:
            try:
                snapshot = await self._catalog.snapshot(run.seed.discovery_snapshot_id)
            except CatalogError:
                pass
            else:
                radio_seed_title = snapshot.snapshot.playlist_title

        contributor_ids = {entry.request.requested_by for entry in state.queue.entries}
        if current_request is not None:
            contributor_ids.add(current_request.requested_by)
        if run is not None:
            contributor_ids.add(run.initiated_by)

        tracks = await self._catalog.tracks(track_ids)
        contributors = await self._users.users(contributor_ids)
        return PlayerReadModel(
            state=state,
            runtime=runtime,
            current_request=current_request,
            tracks=MappingProxyType(tracks),
            contributors=MappingProxyType(contributors),
            radio_seed_track=radio_seed_track,
            radio_seed_title=radio_seed_title,
        )

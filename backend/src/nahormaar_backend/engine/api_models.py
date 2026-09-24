# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Public API data. Runtime, storage and provider implementation details stay private."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.identity import Contributor
from ..domain.access import AccessRole
from ..domain.preferences import Appearance
from .cache import RefreshStatus
from .domain.catalog import MediaReference
from .domain.queue import QueueOrigin
from .domain.sessions import (
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackPhase,
    SessionAction,
)
from .domain.tracks import MediaIdentity


class View(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        allow_inf_nan=False,
        json_schema_serialization_defaults_required=True,
    )


class AccountView(View):
    discord_id: str
    profile: Contributor
    profile_complete: bool
    is_admin: bool
    role: AccessRole
    csrf_token: str
    expires_at: float
    appearance: Appearance


class LogEntryView(View):
    id: int
    timestamp: datetime
    level: str
    source: str
    message: str
    actor_id: str | None
    actor_name: str | None
    trace_id: str | None


class LogsView(View):
    entries: tuple[LogEntryView, ...]


class AccessGrantView(View):
    discord_id: str
    granted_by: str
    granted_at: datetime
    name: str | None


class AccessEventView(View):
    id: UUID
    action: str
    discord_id: str
    actor_id: str
    occurred_at: datetime


class AccessView(View):
    owner_id: str
    admin_ids: tuple[str, ...]
    grants: tuple[AccessGrantView, ...]
    history: tuple[AccessEventView, ...]


class DiscordMemberView(View):
    discord_id: str
    name: str
    display_name: str
    avatar_url: str | None
    guild_id: str
    guild_name: str


class DiscordMembersView(View):
    members: tuple[DiscordMemberView, ...]


class ProfileView(View):
    discord_id: str
    profile: Contributor
    profile_complete: bool
    role: AccessRole
    members: tuple[DiscordMemberView, ...]


class MetadataView(View):
    title: str | None
    artist: str | None
    uploader: str | None
    uploader_url: str | None
    duration_seconds: float | None
    thumbnail_url: str | None


class TrackView(View):
    id: UUID
    identity: MediaIdentity
    source_url: str
    metadata: MetadataView


class OccurrenceView(View):
    id: UUID
    track_id: UUID
    added_by: Contributor | None
    origin: QueueOrigin


class QueueEntryView(OccurrenceView):
    position: int


class HistoryView(OccurrenceView):
    entry_id: UUID
    started_at: datetime
    ended_at: datetime | None
    end_reason: PlaybackEndReason | None


class SessionSettingsView(View):
    id: UUID
    revision: int
    queue_revision: int
    channel_id: str | None
    volume: float
    crossfade_seconds: int


class CheckpointView(View):
    intent: PlaybackIntent
    entry_id: UUID | None
    track_id: UUID | None
    play_id: UUID | None
    position_seconds: float
    added_by: Contributor | None
    origin: QueueOrigin


class PlaybackView(View):
    phase: PlaybackPhase
    attempt_id: UUID | None
    connection: Literal["connected", "connecting", "disconnected"]
    duration_seconds: float | None
    error: str | None


class ManualView(View):
    mode: Literal["manual"] = "manual"


class RadioView(View):
    mode: Literal["radio"] = "radio"
    generation: UUID
    seed: MediaReference
    state: Literal["active", "loading", "waiting"]
    initiator: Contributor
    error: str | None


class SessionView(View):
    session: SessionSettingsView
    queue: tuple[QueueEntryView, ...]
    checkpoint: CheckpointView
    playback: PlaybackView
    history: tuple[HistoryView, ...]
    radio: Annotated[ManualView | RadioView, Field(discriminator="mode")]
    tracks: dict[str, TrackView]


class OutcomeView(View):
    code: str
    added_count: int
    removed_count: int
    restored_count: int
    skipped_count: int
    entries: tuple[OccurrenceView, ...]
    actor: Contributor | None
    undo_id: UUID | None
    undo_expires_at: datetime | None


class MutationView(View):
    request_id: UUID
    action: SessionAction
    state: SessionView
    outcome: OutcomeView
    replayed: bool


class ChangeView(View):
    request_id: UUID | None
    action: SessionAction
    state: SessionView
    outcome: OutcomeView


class ApiError(View):
    code: str
    message: str | None = None
    retryable: bool = False


class ChannelView(View):
    id: str
    name: str
    guild_id: str
    guild_name: str
    can_connect: bool
    can_speak: bool


class CatalogEntryView(View):
    position: int
    track_id: UUID | None
    reference: MediaReference | None
    metadata: MetadataView
    unavailable: str | None


class PlaylistView(View):
    reference: MediaReference
    title: str | None


class DiscoveryView(View):
    version: UUID
    offset: int
    total: int
    next_offset: int | None
    source_has_more: bool
    error: str | None
    playlist: PlaylistView | None
    entries: tuple[CatalogEntryView, ...]
    refresh: RefreshStatus

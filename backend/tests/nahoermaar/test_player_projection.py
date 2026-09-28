# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from nahoermaar.api.player import player_view
from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import (
    DiscoveryResult,
    DiscoverySnapshotId,
    Track,
    TrackId,
    TrackSourceId,
)
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.player.domain import (
    ListeningSessionId,
    PlayerState,
    Queue,
    QueueEntry,
    QueueEntryId,
    RadioRunId,
    VoiceConnectionState,
    new_request,
)
from nahoermaar.player.playback import PlaybackPhase, PlaybackRuntimeState
from nahoermaar.player.read_model import PlayerReader
from nahoermaar.users.domain import DiscordIdentity, User, UserId, UserProfile

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


class Catalog:
    def __init__(self, track: Track) -> None:
        self.track = track

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]:
        assert track_ids == {self.track.id}
        return {self.track.id: self.track}

    async def track_for_source(self, source_id: TrackSourceId) -> Track | None:
        _ = source_id
        return None

    async def snapshot(
        self,
        snapshot_id: DiscoverySnapshotId,
    ) -> DiscoveryResult:
        raise AssertionError(f"Unexpected discovery read: {snapshot_id}")


class Access:
    def __init__(self, users: tuple[User, ...] = ()) -> None:
        self.known = {user.id: user for user in users}

    async def users(self, user_ids: set[UserId]) -> dict[UserId, User]:
        return {
            user_id: self.known[user_id]
            for user_id in user_ids
            if user_id in self.known
        }


def test_runtime_track_remains_visible_during_checkpoint_gap() -> None:
    session_id = ListeningSessionId(uuid4())
    track_id = TrackId(uuid4())
    actor_id = UserId(uuid4())
    track = Track(
        track_id,
        "Runtime track",
        180.0,
        None,
        None,
        None,
        None,
        NOW,
        NOW,
    )
    request = new_request(
        session_id,
        track_id,
        None,
        NOW,
        actor_id=actor_id,
    )
    state = PlayerState.empty(session_id, NOW)
    runtime = PlaybackRuntimeState(
        PlaybackPhase.PLAYING,
        request,
        None,
        None,
        21.5,
        NOW,
        180.0,
        VoiceConnectionState(),
        None,
    )
    reader = PlayerReader(Catalog(track), Access(), lambda: runtime)
    application = cast(
        Application,
        SimpleNamespace(
            player=SimpleNamespace(reader=reader),
            integrations=SimpleNamespace(
                avatars=DiscordAvatarStore(Path("data/avatars"))
            ),
        ),
    )

    view = asyncio.run(player_view(application, state))

    assert view.runtime.phase == PlaybackPhase.PLAYING.value
    assert view.runtime.current is not None
    assert view.runtime.current.id == request.id
    assert view.runtime.current.track.title == "Runtime track"
    assert view.runtime.position_seconds == 21.5


def test_radio_queue_request_keeps_its_requester_without_an_active_radio() -> None:
    session_id = ListeningSessionId(uuid4())
    track_id = TrackId(uuid4())
    actor_id = UserId(uuid4())
    track = Track(
        track_id,
        "Requested by radio",
        180.0,
        None,
        None,
        None,
        None,
        NOW,
        NOW,
    )
    request = new_request(
        session_id,
        track_id,
        None,
        NOW,
        actor_id=actor_id,
        radio_run_id=RadioRunId(uuid4()),
    )
    empty = PlayerState.empty(session_id, NOW)
    state = replace(
        empty,
        queue=Queue(
            session_id,
            1,
            (QueueEntry(QueueEntryId(uuid4()), session_id, request, 0),),
        ),
    )
    actor = User(
        actor_id,
        DiscordIdentity("1377708476259897478", "andrey", None, NOW),
        NOW,
        NOW,
        profile=UserProfile("Andrey"),
    )
    disabled_runtime = PlaybackRuntimeState(
        PlaybackPhase.DISABLED,
        None,
        None,
        None,
        0.0,
        None,
        None,
        VoiceConnectionState(),
        None,
    )
    reader = PlayerReader(Catalog(track), Access((actor,)), lambda: disabled_runtime)
    application = cast(
        Application,
        SimpleNamespace(
            player=SimpleNamespace(reader=reader),
            integrations=SimpleNamespace(
                avatars=DiscordAvatarStore(Path("data/avatars"))
            ),
        ),
    )

    view = asyncio.run(player_view(application, state))

    assert view.radio is None
    assert view.queue[0].request.origin == "radio"
    assert view.queue[0].request.requested_by == actor_id
    assert view.queue[0].request.contributor is not None
    assert view.queue[0].request.contributor.display_name == "Andrey"

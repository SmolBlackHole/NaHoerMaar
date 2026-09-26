# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from nahoermaar.api.player import player_view
from nahoermaar.bootstrap import Application
from nahoermaar.catalog.domain import Track, TrackId
from nahoermaar.player.domain import (
    ListeningSessionId,
    PlayerState,
    VoiceConnectionState,
    new_request,
)
from nahoermaar.player.playback import PlaybackPhase, PlaybackRuntimeState
from nahoermaar.users.domain import User, UserId

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


class Catalog:
    def __init__(self, track: Track) -> None:
        self.track = track

    async def tracks(self, track_ids: set[TrackId]) -> dict[TrackId, Track]:
        assert track_ids == {self.track.id}
        return {self.track.id: self.track}


class Access:
    async def users(self, user_ids: set[UserId]) -> dict[UserId, User]:
        return {}


class Playback:
    def __init__(self, status: PlaybackRuntimeState) -> None:
        self.status = status


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
    application = cast(
        Application,
        SimpleNamespace(
            playback=Playback(runtime),
            catalog=Catalog(track),
            access=Access(),
        ),
    )

    view = asyncio.run(player_view(application, state))

    assert view.runtime.phase == PlaybackPhase.PLAYING.value
    assert view.runtime.current is not None
    assert view.runtime.current.id == request.id
    assert view.runtime.current.track.title == "Runtime track"
    assert view.runtime.position_seconds == 21.5

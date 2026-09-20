# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.playback import PlaybackController
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.models import (
    Contributor,
    HistoryEntry,
    PlayerSnapshot,
    QueueEntry,
)
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import ControlledResolver, FakeVoice


@pytest.mark.parametrize("from_history", [False, True])
def test_requeue_reuses_persisted_metadata_but_gets_new_attribution(
    tmp_path: Path, from_history: bool
) -> None:
    path = tmp_path / "player.db"
    original = QueueEntry(
        "https://www.youtube.com/watch?v=Pqp9fDRp1lw",
        title="Known track",
        video_id="Pqp9fDRp1lw",
        artist="Known artist",
        duration_seconds=123,
        thumbnail_url="https://i.ytimg.com/vi/Pqp9fDRp1lw/hqdefault.jpg",
        added_by=Contributor(uuid4(), "Original author", "0001"),
    )
    with SQLiteStore(path) as store:
        store.save(
            PlayerSnapshot(
                upcoming=() if from_history else (original,),
                recently_played=(HistoryEntry(original, datetime.now(UTC)),)
                if from_history
                else (),
            )
        )

    async def scenario() -> None:
        controller = await PlaybackController.create(
            path, ControlledResolver(), FakeVoice()
        )
        actor = Contributor(uuid4(), "Kai", "0002")
        try:
            result = await controller.request(
                uuid4(),
                commands.Add("https://music.youtube.com/watch?v=Pqp9fDRp1lw", actor),
                actor_id=actor.id,
                actor=actor,
            )
            entry = result.outcome.entries[0]
            assert entry.id != original.id
            assert entry.added_by == actor == result.outcome.actor
            assert (
                entry.title,
                entry.artist,
                entry.duration_seconds,
                entry.thumbnail_url,
            ) == (
                original.title,
                original.artist,
                original.duration_seconds,
                original.thumbnail_url,
            )
            assert controller.snapshot.upcoming[-1] == entry
        finally:
            await controller.close()

    asyncio.run(scenario())

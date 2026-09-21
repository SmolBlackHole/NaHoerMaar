# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from nahormaar_backend.application.metadata import merge_metadata
from nahormaar_backend.application.player import Player
from nahormaar_backend.application.session import Session
from nahormaar_backend.domain import commands
from nahormaar_backend.domain.models import (
    Contributor,
    HistoryEntry,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from nahormaar_backend.persistence.player_store import SQLiteStore
from test_playback import ControlledResolver, FakeVoice


@pytest.mark.parametrize("overwrite", [False, True])
def test_metadata_merge_preserves_identity_and_distinguishes_missing_values(
    overwrite: bool,
) -> None:
    target = QueueEntry(
        "requested source",
        title="Known title",
        video_id="known-id",
        artist="Known artist",
        uploader="Known uploader",
        thumbnail_url="known-cover",
        added_by=Contributor(uuid4(), "Requester", "0001"),
    )
    source = QueueEntry(
        "other source",
        title="Updated title",
        video_id="updated-id",
        artist="",
        duration_seconds=0,
        uploader_url="artist-link",
        origin="radio",
        added_by=Contributor(uuid4(), "Someone else", "0002"),
    )
    assert merge_metadata(target, source, overwrite=overwrite) == replace(
        target,
        title="Updated title" if overwrite else "Known title",
        video_id="updated-id" if overwrite else "known-id",
        artist="" if overwrite else "Known artist",
        duration_seconds=0,
        uploader_url="artist-link",
    )
    metadata = TrackMetadata(title="", duration_seconds=0)
    incoming = TrackMetadata(title="New", duration_seconds=180, thumbnail_url="cover")
    assert merge_metadata(metadata, incoming, overwrite=overwrite) == TrackMetadata(
        title="New" if overwrite else "",
        duration_seconds=180 if overwrite else 0,
        thumbnail_url="cover",
    )


def test_late_metadata_only_updates_the_selected_entry(
    player: Player, store: SQLiteStore
) -> None:
    first = QueueEntry("same source", title="First", artist="Known artist")
    second = QueueEntry("same source", title="Second")
    player.enqueue_many((first, second))
    queue_revision = player.revisions.queue_revision
    updated = replace(first, title="Updated", duration_seconds=0)
    player.enrich(first.id, TrackMetadata(title="Updated", duration_seconds=0))
    assert player.snapshot.upcoming == (updated, second)
    assert player.revisions.queue_revision == queue_revision
    assert player.snapshot == store.load()

    player.remove((updated,))
    before, revisions = player.snapshot, player.revisions
    player.enrich(first.id, TrackMetadata(title="Too late"))
    assert player.snapshot == store.load() == before
    assert player.revisions == revisions


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
        controller = await Session.create(
            lambda: SQLiteStore(path), ControlledResolver(), FakeVoice()
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

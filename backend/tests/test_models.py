# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import FrozenInstanceError

import pytest

from nahormaar_backend.domain.models import PlaybackState, PlayerSnapshot, QueueEntry


def test_source_url_is_enough_to_queue_an_entry() -> None:
    entry = QueueEntry("https://youtu.be/example")
    assert entry.title is None
    assert entry.duration_seconds is None
    assert entry.thumbnail_url is None


@pytest.mark.parametrize("url", ["", " \t"])
def test_empty_source_is_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="source URL"):
        QueueEntry(url)


@pytest.mark.parametrize("duration", [-1.0, float("inf"), float("nan")])
def test_invalid_duration_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="Duration"):
        QueueEntry("https://youtu.be/example", duration_seconds=duration)


def test_snapshots_and_their_entries_are_immutable() -> None:
    entry = QueueEntry("https://youtu.be/example")
    snapshot = PlayerSnapshot(upcoming=(entry,))
    for target, attribute, value in (
        (snapshot, "upcoming", ()),
        (snapshot.upcoming[0], "title", "Changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)


def test_current_entry_cannot_also_be_upcoming() -> None:
    entry = QueueEntry("https://youtu.be/example")
    with pytest.raises(ValueError, match="unique"):
        PlayerSnapshot(PlaybackState.PLAYING, entry, (entry,))


@pytest.mark.parametrize("state", list(PlaybackState))
def test_current_entry_must_match_playback_state(state: PlaybackState) -> None:
    current = (
        QueueEntry("https://youtu.be/example") if state is PlaybackState.IDLE else None
    )
    with pytest.raises(ValueError, match="current entry"):
        PlayerSnapshot(state=state, current=current)

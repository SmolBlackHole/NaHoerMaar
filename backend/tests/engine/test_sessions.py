# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from nahormaar_backend.engine.domain.queue import Contributor, QueueOrigin
from nahormaar_backend.engine.domain.sessions import (
    ListeningSession,
    PlaybackCheckpoint,
    PlaybackEndReason,
    PlaybackIntent,
    PlaybackRecord,
)


@pytest.mark.parametrize("channel", [0, -1, True, 1.5])
def test_reconnect_target_requires_a_valid_channel(channel: object) -> None:
    with pytest.raises(ValueError, match="channel"):
        ListeningSession(channel_id=cast(int, channel))


@pytest.mark.parametrize("volume", [-0.01, 1.01, float("inf"), float("nan")])
def test_invalid_session_volume_is_rejected(volume: float) -> None:
    with pytest.raises(ValueError, match="volume"):
        ListeningSession(volume=volume)


def test_confirmation_and_replay_have_independent_play_identity() -> None:
    original = PlaybackRecord(uuid4(), uuid4(), uuid4(), datetime.now(UTC))
    finished = replace(
        original,
        ended_at=original.started_at + timedelta(seconds=120),
        end_reason=PlaybackEndReason.COMPLETED,
    )
    replay = replace(original, id=uuid4())
    assert finished.id == original.id
    assert finished.entry_id == replay.entry_id == original.entry_id
    assert replay.id != original.id
    assert original.ended_at is None


@pytest.mark.parametrize("reason", list(PlaybackEndReason))
def test_confirmed_play_retains_its_end_reason(reason: PlaybackEndReason) -> None:
    started = datetime.now(UTC)
    record = PlaybackRecord(
        uuid4(), uuid4(), uuid4(), started, ended_at=started, end_reason=reason
    )
    assert record.end_reason is reason


def test_unconfirmed_progress_can_be_restored_without_claiming_a_listen() -> None:
    owner = ListeningSession(channel_id=123, volume=0.5)
    current = PlaybackCheckpoint(
        owner.id,
        intent=PlaybackIntent.PAUSED,
        entry_id=uuid4(),
        track_id=uuid4(),
        position_seconds=32.25,
        added_by=Contributor(uuid4(), "Имя", "abcd"),
        origin=QueueOrigin.RADIO,
    )
    assert current.play_id is None
    confirmed = replace(current, play_id=uuid4())
    resumed = replace(confirmed, intent=PlaybackIntent.PLAYING, position_seconds=40)
    assert resumed.play_id == confirmed.play_id
    assert resumed.entry_id == current.entry_id
    assert resumed.added_by == current.added_by
    assert resumed.origin is QueueOrigin.RADIO
    assert replace(owner, channel_id=None).id == owner.id


def test_playback_times_require_an_aware_ordered_pair_and_end_reason() -> None:
    record = PlaybackRecord(uuid4(), uuid4(), uuid4(), datetime.now(UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(record, started_at=datetime(2026, 9, 21))
    with pytest.raises(ValueError, match="together"):
        replace(record, ended_at=record.started_at)
    with pytest.raises(ValueError, match="together"):
        replace(record, end_reason=PlaybackEndReason.SKIPPED)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(
            record,
            ended_at=datetime(2026, 9, 21),
            end_reason=PlaybackEndReason.SKIPPED,
        )
    with pytest.raises(ValueError, match="before"):
        replace(
            record,
            ended_at=record.started_at - timedelta(seconds=1),
            end_reason=PlaybackEndReason.COMPLETED,
        )
    with pytest.raises(ValueError, match="end reason"):
        replace(
            record,
            ended_at=record.started_at,
            end_reason=cast(PlaybackEndReason, "skipped"),
        )
    with pytest.raises(ValueError, match="origin"):
        replace(record, origin=cast(QueueOrigin, "manual"))


@pytest.mark.parametrize("position", [-1, float("inf"), float("nan")])
def test_invalid_checkpoint_progress_is_rejected(position: float) -> None:
    with pytest.raises(ValueError, match="position"):
        PlaybackCheckpoint(
            uuid4(),
            PlaybackIntent.PLAYING,
            entry_id=uuid4(),
            track_id=uuid4(),
            position_seconds=position,
        )


def test_checkpoint_intent_and_current_entry_must_agree() -> None:
    stopped = PlaybackCheckpoint(uuid4())
    assert stopped.intent is PlaybackIntent.STOPPED
    assert stopped.entry_id is stopped.play_id is stopped.track_id is None
    assert stopped.position_seconds == 0
    invalid: tuple[Callable[[], PlaybackCheckpoint], ...] = (
        lambda: replace(stopped, intent=cast(PlaybackIntent, "playing")),
        lambda: replace(stopped, origin=cast(QueueOrigin, "manual")),
        lambda: replace(stopped, intent=PlaybackIntent.PLAYING),
        lambda: replace(stopped, intent=PlaybackIntent.PAUSED, entry_id=uuid4()),
        lambda: replace(stopped, intent=PlaybackIntent.PLAYING, track_id=uuid4()),
        lambda: replace(stopped, entry_id=uuid4(), track_id=uuid4()),
        lambda: replace(stopped, play_id=uuid4()),
        lambda: replace(stopped, position_seconds=1),
        lambda: replace(stopped, added_by=Contributor(uuid4(), "Kai", "abcd")),
        lambda: replace(stopped, origin=QueueOrigin.RADIO),
    )
    for create in invalid:
        with pytest.raises(ValueError):
            create()


def test_persisted_session_values_are_immutable() -> None:
    owner = ListeningSession()
    record = PlaybackRecord(owner.id, uuid4(), uuid4(), datetime.now(UTC))
    checkpoint = PlaybackCheckpoint(owner.id)
    for target, name, value in (
        (owner, "channel_id", 123),
        (record, "entry_id", uuid4()),
        (checkpoint, "position_seconds", 5),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, value)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from dataclasses import FrozenInstanceError, replace
from typing import cast
from uuid import uuid4

import pytest

from nahormaar_backend.engine.domain.queue import Contributor, QueueEntry, QueueOrigin


def test_repeated_tracks_have_independent_queue_entry_ids() -> None:
    session_id, track_id = uuid4(), uuid4()
    first = QueueEntry(session_id, track_id, 0)
    second = QueueEntry(session_id, track_id, 1)
    assert first.track_id == second.track_id == track_id
    assert first.session_id == second.session_id == session_id
    assert first.id != second.id
    assert first.origin is QueueOrigin.MANUAL
    assert first.added_by is None


def test_new_position_preserves_entry_identity_and_attribution() -> None:
    contributor = Contributor(uuid4(), "Kai", "a3f0")
    entry = QueueEntry(
        uuid4(), uuid4(), 0, added_by=contributor, origin=QueueOrigin.RADIO
    )
    moved = replace(entry, position=12)
    assert moved.id == entry.id
    assert moved.track_id == entry.track_id
    assert moved.session_id == entry.session_id
    assert moved.added_by == contributor
    assert moved.origin is QueueOrigin.RADIO
    assert moved.position == 12
    assert entry.position == 0


@pytest.mark.parametrize("position", [-1, 1.5, float("nan"), True])
def test_position_requires_a_non_negative_integer(position: object) -> None:
    with pytest.raises(ValueError, match="position"):
        QueueEntry(uuid4(), uuid4(), cast(int, position))


@pytest.mark.parametrize("origin", ["manual", "automatic", None])
def test_origin_requires_the_domain_enum(origin: object) -> None:
    with pytest.raises(ValueError, match="origin"):
        QueueEntry(uuid4(), uuid4(), 0, origin=cast(QueueOrigin, origin))


@pytest.mark.parametrize("name", ["", " padded", "padded ", "a" * 33])
def test_invalid_contributor_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        Contributor(uuid4(), name, "0000")


@pytest.mark.parametrize("avatar", ["", "123", "12345", "abcd\n", "G123"])
def test_invalid_contributor_avatars_are_rejected(avatar: str) -> None:
    with pytest.raises(ValueError, match="avatar"):
        Contributor(uuid4(), "User", avatar)


def test_entry_and_contributor_are_immutable() -> None:
    contributor = Contributor(uuid4(), "Имя", "0000")
    entry = QueueEntry(uuid4(), uuid4(), 0, added_by=contributor)
    for target, attribute, value in (
        (entry, "track_id", uuid4()),
        (contributor, "name", "Changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)

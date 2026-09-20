# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Iterator
from pathlib import Path

import pytest

from nahormaar_backend.application.player import Player
from nahormaar_backend.persistence.player_store import SQLiteStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    with SQLiteStore(tmp_path / "player.sqlite3", timeout=0) as database:
        yield database


@pytest.fixture
def player(store: SQLiteStore) -> Player:
    return Player(store)

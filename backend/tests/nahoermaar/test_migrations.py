# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_uses_new_schema_without_placeholder_revision() -> None:
    root = Path(__file__).parents[3]
    configuration = Config(root / "alembic.ini")
    scripts = ScriptDirectory.from_config(configuration)

    assert (
        Path(scripts.dir).resolve()
        == (root / "backend/src/nahoermaar/database/migrations").resolve()
    )
    assert scripts.get_heads() == []

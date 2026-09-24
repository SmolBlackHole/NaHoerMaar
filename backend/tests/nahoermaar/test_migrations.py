# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from engine.database import database_url


def test_alembic_uses_new_schema_with_single_initial_revision() -> None:
    root = Path(__file__).parents[3]
    configuration = Config(root / "alembic.ini")
    scripts = ScriptDirectory.from_config(configuration)

    assert (
        Path(scripts.dir).resolve()
        == (root / "backend/src/nahoermaar/database/migrations").resolve()
    )
    assert scripts.get_heads() == ["0001_initial"]


def test_initial_migration_round_trip(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    configuration = Config(root / "alembic.ini")
    url = database_url(tmp_path / "migration-round-trip")
    configuration.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    command.upgrade(configuration, "head")
    command.check(configuration)
    command.downgrade(configuration, "base")
    command.upgrade(configuration, "head")
    command.check(configuration)

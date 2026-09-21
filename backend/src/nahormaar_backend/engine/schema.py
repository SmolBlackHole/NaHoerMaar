# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Explicit engine schema management, never invoked by the old runtime."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, MetaData

from ..persistence import models as models  # register unchanged account tables
from ..persistence.database import Base as AccountBase
from .persistence import Base

REVISION = "engine_0001"


def metadata() -> MetaData:
    """Account storage is unchanged; playback uses only the new engine tables."""
    result = MetaData()
    for table in (
        *Base.metadata.sorted_tables,
        AccountBase.metadata.tables["accounts"],
        AccountBase.metadata.tables["sessions"],
        AccountBase.metadata.tables["login_attempts"],
    ):
        table.to_metadata(result)
    return result


def upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).with_name("migrations"))
    )
    config.attributes["connection"] = connection
    command.upgrade(config, REVISION)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Track contributors."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    for table in ("queue_entries", "playback_history"):
        op.add_column(table, sa.Column("added_by", sa.JSON()))


def downgrade() -> None:
    for table in ("queue_entries", "playback_history"):
        op.drop_column(table, "added_by")

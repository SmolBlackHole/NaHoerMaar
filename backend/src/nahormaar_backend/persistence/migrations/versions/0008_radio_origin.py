# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Keep automatic queue attribution in queue and playback history."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    for table in ("queue_entries", "playback_history"):
        op.add_column(
            table,
            sa.Column("origin", sa.String(), nullable=False, server_default="manual"),
        )


def downgrade() -> None:
    for table in ("queue_entries", "playback_history"):
        op.drop_column(table, "origin")

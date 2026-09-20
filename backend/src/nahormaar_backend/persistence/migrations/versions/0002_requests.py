# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Player revisions and idempotent requests."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    for name in ("revision", "queue_revision"):
        op.add_column(
            "player_state",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("code", sa.String()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("entry_id", sa.Uuid()),
    )


def downgrade() -> None:
    op.drop_table("requests")
    op.drop_column("player_state", "queue_revision")
    op.drop_column("player_state", "revision")

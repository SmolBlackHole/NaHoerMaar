# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist the active radio strategy alongside the listening session."""

import sqlalchemy as sa
from alembic import op

revision = "engine_0002"
down_revision = "engine_0001"


def upgrade() -> None:
    op.create_table(
        "radio_strategies",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("strategy", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["listening_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )


def downgrade() -> None:
    raise RuntimeError("Radio strategy data cannot be downgraded.")

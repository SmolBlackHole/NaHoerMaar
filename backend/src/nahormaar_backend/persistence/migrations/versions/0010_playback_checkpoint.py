# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Remember the voice channel and playback position across restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.create_table(
        "playback_checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.CheckConstraint("id = 1"),
    )


def downgrade() -> None:
    op.drop_table("playback_checkpoint")

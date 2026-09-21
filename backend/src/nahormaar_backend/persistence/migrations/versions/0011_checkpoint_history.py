# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist confirmation for the current logical play, independently of entry IDs."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.add_column(
        "playback_checkpoint",
        sa.Column("history_recorded", sa.Boolean(), nullable=False, server_default="0"),
    )
    # Old checkpoints cannot distinguish a replay of the same entry. Preserve
    # their previous history-based inference once; new checkpoints are explicit.
    op.execute(
        sa.text("""
        UPDATE playback_checkpoint
        SET history_recorded = EXISTS (
            SELECT 1 FROM playback_history
            WHERE playback_history.entry_id = playback_checkpoint.entry_id
        )
    """)
    )


def downgrade() -> None:
    op.drop_column("playback_checkpoint", "history_recorded")

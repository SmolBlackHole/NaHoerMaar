# SPDX-FileCopyrightText: 2026 SmolBlackHole
# SPDX-License-Identifier: MPL-2.0

"""Initial persistent queue and player."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "queue_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("video_id", sa.String()),
        sa.Column("title", sa.String()),
        sa.Column("uploader", sa.String()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("thumbnail_url", sa.String()),
        sa.CheckConstraint("position >= 0"),
    )
    player = op.create_table(
        "player_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("current_entry_id", sa.Uuid(), sa.ForeignKey("queue_entries.id")),
        sa.CheckConstraint("id = 1"),
    )
    op.bulk_insert(player, [{"id": 1, "state": "idle"}])


def downgrade() -> None:
    op.drop_table("player_state")
    op.drop_table("queue_entries")

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Artist metadata and playback history."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.add_column("queue_entries", sa.Column("artist", sa.String()))
    op.add_column("queue_entries", sa.Column("uploader_url", sa.String()))
    op.create_table(
        "playback_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, unique=True),
        sa.Column("played_at", sa.String(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("video_id", sa.String()),
        sa.Column("title", sa.String()),
        sa.Column("uploader", sa.String()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("thumbnail_url", sa.String()),
        sa.Column("artist", sa.String()),
        sa.Column("uploader_url", sa.String()),
    )


def downgrade() -> None:
    op.drop_table("playback_history")
    op.drop_column("queue_entries", "uploader_url")
    op.drop_column("queue_entries", "artist")

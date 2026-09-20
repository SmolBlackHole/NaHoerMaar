# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Short-lived queue undo and replayable mutation details."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.add_column("requests", sa.Column("details", sa.JSON(), nullable=True))
    op.create_table(
        "queue_undo",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_queue_undo_expires_at", "queue_undo", ["expires_at"])


def downgrade() -> None:
    op.drop_table("queue_undo")
    op.drop_column("requests", "details")

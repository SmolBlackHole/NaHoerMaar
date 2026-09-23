# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Store Discord channel snowflakes as 64-bit integers."""

import sqlalchemy as sa
from alembic import op

revision = "engine_0003"
down_revision = "engine_0002"


def upgrade() -> None:
    op.alter_column(
        "listening_sessions",
        "channel_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    raise RuntimeError("Discord channel IDs cannot safely be narrowed to INTEGER.")

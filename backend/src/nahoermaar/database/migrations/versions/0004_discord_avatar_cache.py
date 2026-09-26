# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Remove locally selected Pixabot avatars."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_discord_avatar_cache"
down_revision: str | None = "0003_radio_request_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_user_profiles_pixabot_valid",
        "user_profiles",
        type_="check",
    )
    op.drop_column("user_profiles", "pixabot")


def downgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("pixabot", sa.String(length=4), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_profiles_pixabot_valid",
        "user_profiles",
        "pixabot IS NULL OR pixabot ~ '^[0-9a-f]{4}$'",
    )

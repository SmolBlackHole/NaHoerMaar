# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Index normalized catalog names used by local discovery."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_catalog_search_indexes"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artists_name_lower",
        "artists",
        [sa.text("lower(name)")],
    )
    op.create_index(
        "ix_tracks_title_lower",
        "tracks",
        [sa.text("lower(title)")],
    )


def downgrade() -> None:
    op.drop_index("ix_tracks_title_lower", table_name="tracks")
    op.drop_index("ix_artists_name_lower", table_name="artists")

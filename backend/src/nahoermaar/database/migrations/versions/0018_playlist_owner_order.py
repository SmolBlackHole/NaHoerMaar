# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist the owner's playlist-card order."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0018_playlist_owner_order"
down_revision: str | None = "0017_playlist_entry_undos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playlists",
        sa.Column("owner_position", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY owner_id
                    ORDER BY updated_at DESC, id DESC
                ) - 1 AS owner_position
            FROM playlists
        )
        UPDATE playlists
        SET owner_position = ordered.owner_position
        FROM ordered
        WHERE playlists.id = ordered.id
        """
    )
    op.alter_column("playlists", "owner_position", nullable=False)
    op.create_check_constraint(
        op.f("ck_playlists_owner_position_nonnegative"),
        "playlists",
        "owner_position >= 0",
    )
    op.create_unique_constraint(
        op.f("uq_playlists_owner_position"),
        "playlists",
        ["owner_id", "owner_position"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_playlists_owner_position"),
        "playlists",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_playlists_owner_position_nonnegative"),
        "playlists",
        type_="check",
    )
    op.drop_column("playlists", "owner_position")

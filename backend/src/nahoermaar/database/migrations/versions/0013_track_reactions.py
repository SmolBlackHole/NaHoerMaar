# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add personal reactions for canonical Catalog tracks."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0013_track_reactions"
down_revision: str | None = "0012_track_lyrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_reactions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column(
            "value",
            sa.Enum(
                "like",
                "dislike",
                name="track_reaction_value",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_track_reactions_update_not_before_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_track_reactions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_reactions_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "track_id",
            name=op.f("pk_track_reactions"),
        ),
    )
    op.create_index(
        "ix_track_reactions_user_value_updated_track",
        "track_reactions",
        ["user_id", "value", "updated_at", "track_id"],
        unique=False,
    )
    op.create_index(
        "ix_track_reactions_track_value",
        "track_reactions",
        ["track_id", "value"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_track_reactions_track_value",
        table_name="track_reactions",
    )
    op.drop_index(
        "ix_track_reactions_user_value_updated_track",
        table_name="track_reactions",
    )
    op.drop_table("track_reactions")

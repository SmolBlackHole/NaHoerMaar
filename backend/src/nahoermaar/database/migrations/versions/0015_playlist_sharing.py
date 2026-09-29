# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add playlist visibility and explicit collaborators."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0015_playlist_sharing"
down_revision: str | None = "0014_personal_playlists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playlists",
        sa.Column(
            "visibility",
            sa.Enum(
                "private",
                "collaborators",
                "public",
                name="playlist_visibility",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="private",
        ),
    )
    op.alter_column("playlists", "visibility", server_default=None)
    op.create_index(
        "ix_playlists_visibility_updated_id",
        "playlists",
        ["visibility", "updated_at", "id"],
        unique=False,
    )
    op.create_table(
        "playlist_collaborators",
        sa.Column("playlist_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["playlist_id"],
            ["playlists.id"],
            name=op.f("fk_playlist_collaborators_playlist_id_playlists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_playlist_collaborators_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            name=op.f("fk_playlist_collaborators_granted_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "playlist_id",
            "user_id",
            name=op.f("pk_playlist_collaborators"),
        ),
    )
    op.create_index(
        "ix_playlist_collaborators_user_playlist",
        "playlist_collaborators",
        ["user_id", "playlist_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playlist_collaborators_user_playlist",
        table_name="playlist_collaborators",
    )
    op.drop_table("playlist_collaborators")
    op.drop_index("ix_playlists_visibility_updated_id", table_name="playlists")
    op.drop_column("playlists", "visibility")

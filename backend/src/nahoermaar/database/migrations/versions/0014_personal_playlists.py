# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add bounded personal playlists and ordered entries."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0014_personal_playlists"
down_revision: str | None = "0013_track_reactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playlists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND name = btrim(name)",
            name=op.f("ck_playlists_name_valid"),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name=op.f("ck_playlists_revision_nonnegative"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_playlists_update_not_before_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_playlists_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlists")),
    )
    op.create_index(
        "ix_playlists_owner_updated_id",
        "playlists",
        ["owner_id", "updated_at", "id"],
        unique=False,
    )

    op.create_table(
        "playlist_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("playlist_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("preferred_source_id", sa.Uuid(), nullable=True),
        sa.Column("added_by", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_playlist_entries_position_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_playlist_entries_added_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["playlist_id"],
            ["playlists.id"],
            name=op.f("fk_playlist_entries_playlist_id_playlists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["preferred_source_id"],
            ["track_sources.id"],
            name=op.f("fk_playlist_entries_preferred_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_playlist_entries_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_entries")),
        sa.UniqueConstraint(
            "playlist_id",
            "position",
            name=op.f("uq_playlist_entries_playlist_position"),
        ),
    )
    op.create_index(
        "ix_playlist_entries_track_id",
        "playlist_entries",
        ["track_id"],
        unique=False,
    )
    op.create_index(
        "ix_playlist_entries_preferred_source_id",
        "playlist_entries",
        ["preferred_source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playlist_entries_preferred_source_id",
        table_name="playlist_entries",
    )
    op.drop_index(
        "ix_playlist_entries_track_id",
        table_name="playlist_entries",
    )
    op.drop_table("playlist_entries")
    op.drop_index("ix_playlists_owner_updated_id", table_name="playlists")
    op.drop_table("playlists")

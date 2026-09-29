# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Store short-lived playlist entry undo receipts."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0017_playlist_entry_undos"
down_revision: str | None = "0016_linked_playlists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playlist_entry_undos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("playlist_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("preferred_source_id", sa.Uuid(), nullable=True),
        sa.Column("added_by", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entry_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_playlist_entry_undos_position_nonnegative")
        ),
        sa.CheckConstraint(
            "expires_at > removed_at",
            name=op.f("ck_playlist_entry_undos_positive_lifetime"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_playlist_entry_undos_actor_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["playlist_id"],
            ["playlists.id"],
            name=op.f("fk_playlist_entry_undos_playlist_id_playlists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_playlist_entry_undos_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preferred_source_id"],
            ["track_sources.id"],
            name=op.f("fk_playlist_entry_undos_preferred_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_playlist_entry_undos_added_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_entry_undos")),
    )
    op.create_index(
        "ix_playlist_entry_undos_expires_at",
        "playlist_entry_undos",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playlist_entry_undos_expires_at",
        table_name="playlist_entry_undos",
    )
    op.drop_table("playlist_entry_undos")

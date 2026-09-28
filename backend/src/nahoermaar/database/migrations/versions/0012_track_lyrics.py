# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add bounded lyrics cache for canonical tracks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_track_lyrics"
down_revision: str | None = "0011_player_settings_action"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_lyrics",
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("metadata_signature", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "available",
                "instrumental",
                "not_found",
                name="lyrics_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider_record_id", sa.BigInteger(), nullable=True),
        sa.Column("plain_lyrics", sa.Text(), nullable=True),
        sa.Column("synced_lyrics", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(metadata_signature) = 64",
            name=op.f("ck_track_lyrics_metadata_signature_sha256"),
        ),
        sa.CheckConstraint(
            "expires_at > fetched_at",
            name=op.f("ck_track_lyrics_expiry_after_fetch"),
        ),
        sa.CheckConstraint(
            "("
            "(state = 'available' AND provider_record_id IS NOT NULL "
            "AND (plain_lyrics IS NOT NULL OR synced_lyrics IS NOT NULL)) OR "
            "(state = 'instrumental' AND provider_record_id IS NOT NULL "
            "AND plain_lyrics IS NULL AND synced_lyrics IS NULL) OR "
            "(state = 'not_found' AND provider_record_id IS NULL "
            "AND plain_lyrics IS NULL AND synced_lyrics IS NULL)"
            ")",
            name=op.f("ck_track_lyrics_payload_matches_state"),
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_lyrics_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("track_id", name=op.f("pk_track_lyrics")),
    )
    op.create_index(
        op.f("ix_track_lyrics_expires_at"),
        "track_lyrics",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_track_lyrics_expires_at"), table_name="track_lyrics")
    op.drop_table("track_lyrics")

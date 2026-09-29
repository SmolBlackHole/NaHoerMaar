# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add occurrence-preserving linked playlist state."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0016_linked_playlists"
down_revision: str | None = "0015_playlist_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playlists",
        sa.Column("source_provider_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "playlists",
        sa.Column("source_external_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "playlists",
        sa.Column("source_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "playlists",
        sa.Column("source_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "playlists",
        sa.Column(
            "source_last_successful_sync_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "playlists",
        sa.Column("source_last_error_code", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "playlists",
        sa.Column(
            "source_unavailable_entry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "playlists",
        sa.Column(
            "source_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "playlists",
        "source_unavailable_entry_count",
        server_default=None,
    )
    op.alter_column("playlists", "source_truncated", server_default=None)
    op.create_check_constraint(
        op.f("ck_playlists_source_identity_complete"),
        "playlists",
        "((source_provider_key IS NULL AND source_external_id IS NULL AND "
        "source_url IS NULL) OR (source_provider_key IS NOT NULL AND "
        "source_external_id IS NOT NULL AND source_url IS NOT NULL))",
    )
    op.create_check_constraint(
        op.f("ck_playlists_source_unavailable_nonnegative"),
        "playlists",
        "source_unavailable_entry_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_playlists_source_state_complete"),
        "playlists",
        "((source_provider_key IS NULL AND source_last_attempt_at IS NULL AND "
        "source_last_successful_sync_at IS NULL AND source_last_error_code IS NULL "
        "AND source_unavailable_entry_count = 0 AND source_truncated = false) OR "
        "(source_provider_key IS NOT NULL AND source_last_attempt_at IS NOT NULL "
        "AND source_last_successful_sync_at IS NOT NULL))",
    )
    op.create_check_constraint(
        op.f("ck_playlists_source_success_not_after_attempt"),
        "playlists",
        "source_last_successful_sync_at IS NULL OR source_last_attempt_at IS NULL "
        "OR source_last_successful_sync_at <= source_last_attempt_at",
    )
    op.create_unique_constraint(
        op.f("uq_playlists_owner_source"),
        "playlists",
        ["owner_id", "source_provider_key", "source_external_id"],
    )
    op.create_index(
        "ix_playlists_source_due",
        "playlists",
        ["source_provider_key", "source_last_successful_sync_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_playlists_source_due", table_name="playlists")
    op.drop_constraint(
        op.f("uq_playlists_owner_source"),
        "playlists",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_playlists_source_success_not_after_attempt"),
        "playlists",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_playlists_source_unavailable_nonnegative"),
        "playlists",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_playlists_source_state_complete"),
        "playlists",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_playlists_source_identity_complete"),
        "playlists",
        type_="check",
    )
    op.drop_column("playlists", "source_truncated")
    op.drop_column("playlists", "source_unavailable_entry_count")
    op.drop_column("playlists", "source_last_error_code")
    op.drop_column("playlists", "source_last_successful_sync_at")
    op.drop_column("playlists", "source_last_attempt_at")
    op.drop_column("playlists", "source_url")
    op.drop_column("playlists", "source_external_id")
    op.drop_column("playlists", "source_provider_key")

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist concrete work performed by background job runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_background_job_details"
down_revision: str | None = "0007_background_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_job_run_details",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "track_metadata",
                "discovery_refresh",
                "data_cleanup",
                name="background_job_run_detail_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "changed",
                "unchanged",
                "failed",
                "skipped",
                name="background_job_run_detail_outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_background_job_run_details_position_non_negative"),
        ),
        sa.CheckConstraint(
            "affected_count >= 0",
            name=op.f("ck_background_job_run_details_affected_count_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["background_job_runs.id"],
            name=op.f("fk_background_job_run_details_run_id_background_job_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job_run_details")),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name=op.f("uq_background_job_run_details_run_id"),
        ),
    )
    op.create_index(
        "ix_background_job_run_details_run_position",
        "background_job_run_details",
        ["run_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_job_run_details_run_position",
        table_name="background_job_run_details",
    )
    op.drop_table("background_job_run_details")

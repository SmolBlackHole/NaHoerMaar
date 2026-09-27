# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist bounded background job runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_background_jobs"
down_revision: str | None = "0006_unattended_playback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_job_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "job_id",
            sa.Enum(
                "catalog-maintenance",
                "housekeeping",
                name="background_job_id",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "trigger",
            sa.Enum(
                "scheduled",
                "manual",
                name="background_job_trigger",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                "cancelled",
                name="background_job_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "requested_count >= 0",
            name=op.f("ck_background_job_runs_requested_count_non_negative"),
        ),
        sa.CheckConstraint(
            "candidate_count >= 0",
            name=op.f("ck_background_job_runs_candidate_count_non_negative"),
        ),
        sa.CheckConstraint(
            "processed_count >= 0",
            name=op.f("ck_background_job_runs_processed_count_non_negative"),
        ),
        sa.CheckConstraint(
            "changed_count >= 0",
            name=op.f("ck_background_job_runs_changed_count_non_negative"),
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name=op.f("ck_background_job_runs_failure_count_non_negative"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status <> 'running' AND finished_at IS NOT NULL)",
            name=op.f("ck_background_job_runs_finished_state_valid"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_background_job_runs_finish_not_before_start"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_background_job_runs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job_runs")),
    )
    op.create_index(
        op.f("ix_background_job_runs_status"),
        "background_job_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_background_job_runs_requested_by"),
        "background_job_runs",
        ["requested_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_background_job_runs_started_at"),
        "background_job_runs",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_background_job_runs_job_started",
        "background_job_runs",
        ["job_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_job_runs_job_started",
        table_name="background_job_runs",
    )
    op.drop_index(
        op.f("ix_background_job_runs_started_at"),
        table_name="background_job_runs",
    )
    op.drop_index(
        op.f("ix_background_job_runs_requested_by"),
        table_name="background_job_runs",
    )
    op.drop_index(
        op.f("ix_background_job_runs_status"),
        table_name="background_job_runs",
    )
    op.drop_table("background_job_runs")

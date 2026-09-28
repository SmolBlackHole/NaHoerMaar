# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist provider-source retry state and register source revalidation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_source_revalidation_job"
down_revision: str | None = "0009_catalog_cleanup_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "track_sources",
        sa.Column(
            "failure_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "track_sources",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "track_sources",
        sa.Column("last_failure_code", sa.String(length=120), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_track_sources_failure_count_non_negative"),
        "track_sources",
        "failure_count >= 0",
    )
    op.create_index(
        op.f("ix_track_sources_retry_at"),
        "track_sources",
        ["retry_at"],
        unique=False,
    )

    op.drop_constraint(
        "background_job_id",
        "background_job_runs",
        type_="check",
    )
    op.create_check_constraint(
        "background_job_id",
        "background_job_runs",
        "job_id IN ('catalog-maintenance', 'catalog-cleanup', "
        "'source-revalidation', 'housekeeping')",
    )
    op.drop_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        type_="check",
    )
    op.alter_column(
        "background_job_run_details",
        "kind",
        existing_type=sa.String(length=17),
        type_=sa.String(length=19),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        "kind IN ('track_metadata', 'discovery_refresh', "
        "'source_revalidation', 'data_cleanup')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM background_job_runs WHERE job_id = 'source-revalidation'")
    op.drop_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        type_="check",
    )
    op.alter_column(
        "background_job_run_details",
        "kind",
        existing_type=sa.String(length=19),
        type_=sa.String(length=17),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        "kind IN ('track_metadata', 'discovery_refresh', 'data_cleanup')",
    )
    op.drop_constraint(
        "background_job_id",
        "background_job_runs",
        type_="check",
    )
    op.create_check_constraint(
        "background_job_id",
        "background_job_runs",
        "job_id IN ('catalog-maintenance', 'catalog-cleanup', 'housekeeping')",
    )

    op.drop_index(op.f("ix_track_sources_retry_at"), table_name="track_sources")
    op.drop_constraint(
        op.f("ck_track_sources_failure_count_non_negative"),
        "track_sources",
        type_="check",
    )
    op.drop_column("track_sources", "last_failure_code")
    op.drop_column("track_sources", "retry_at")
    op.drop_column("track_sources", "failure_count")

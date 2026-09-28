# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Register the catalog cleanup job identifier."""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_catalog_cleanup_job"
down_revision: str | None = "0008_background_job_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("DELETE FROM background_job_runs WHERE job_id = 'catalog-cleanup'")
    op.drop_constraint(
        "background_job_id",
        "background_job_runs",
        type_="check",
    )
    op.create_check_constraint(
        "background_job_id",
        "background_job_runs",
        "job_id IN ('catalog-maintenance', 'housekeeping')",
    )

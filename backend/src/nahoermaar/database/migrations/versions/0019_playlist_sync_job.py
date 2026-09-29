# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Register linked-playlist synchronization job records."""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_playlist_sync_job"
down_revision: str | None = "0018_playlist_owner_order"
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
        "job_id IN ('catalog-maintenance', 'catalog-cleanup', "
        "'source-revalidation', 'playlist-sync', 'housekeeping')",
    )
    op.drop_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        type_="check",
    )
    op.create_check_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        "kind IN ('track_metadata', 'discovery_refresh', "
        "'source_revalidation', 'playlist_sync', 'data_cleanup')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM background_job_runs WHERE job_id = 'playlist-sync'")
    op.drop_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        type_="check",
    )
    op.create_check_constraint(
        "background_job_run_detail_kind",
        "background_job_run_details",
        "kind IN ('track_metadata', 'discovery_refresh', "
        "'source_revalidation', 'data_cleanup')",
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

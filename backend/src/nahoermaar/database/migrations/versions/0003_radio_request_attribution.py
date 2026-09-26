# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist the user represented by every radio queue request."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_radio_request_attribution"
down_revision: str | None = "0002_catalog_search_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_track_requests_origin_owner"),
        "track_requests",
        type_="check",
    )
    op.execute(
        """
        UPDATE track_requests AS request
        SET requested_by = radio.initiated_by
        FROM radio_runs AS radio
        WHERE request.radio_run_id = radio.id
          AND request.origin = 'radio'
          AND request.requested_by IS NULL
        """
    )
    op.alter_column("track_requests", "requested_by", nullable=False)
    op.create_check_constraint(
        op.f("ck_track_requests_origin_owner"),
        "track_requests",
        "(origin = 'manual' AND requested_by IS NOT NULL AND radio_run_id IS NULL) "
        "OR (origin = 'radio' AND requested_by IS NOT NULL "
        "AND radio_run_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_track_requests_origin_owner"),
        "track_requests",
        type_="check",
    )
    op.alter_column("track_requests", "requested_by", nullable=True)
    op.execute(
        """
        UPDATE track_requests
        SET requested_by = NULL
        WHERE origin = 'radio'
        """
    )
    op.create_check_constraint(
        op.f("ck_track_requests_origin_owner"),
        "track_requests",
        "(origin = 'manual' AND requested_by IS NOT NULL AND radio_run_id IS NULL) "
        "OR (origin = 'radio' AND requested_by IS NULL "
        "AND radio_run_id IS NOT NULL)",
    )

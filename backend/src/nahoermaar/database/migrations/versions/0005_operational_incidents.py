# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Add short-lived structured operational incidents."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_operational_incidents"
down_revision: str | None = "0004_discord_avatar_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "warning",
                "error",
                "critical",
                name="incident_severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "rejected",
                "failed",
                "retry",
                "recovered",
                name="incident_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("component", sa.String(length=80), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("operation_type", sa.String(length=160), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(
                "user",
                "system",
                name="incident_trigger",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_operational_incidents_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_incidents")),
    )
    op.create_index(
        op.f("ix_operational_incidents_occurred_at"),
        "operational_incidents",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operational_incidents_actor_id"),
        "operational_incidents",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operational_incidents_operation_type"),
        "operational_incidents",
        ["operation_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operational_incidents_correlation_id"),
        "operational_incidents",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_operational_incidents_component_code",
        "operational_incidents",
        ["component", "error_code"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operational_incidents_component_code",
        table_name="operational_incidents",
    )
    op.drop_index(
        op.f("ix_operational_incidents_correlation_id"),
        table_name="operational_incidents",
    )
    op.drop_index(
        op.f("ix_operational_incidents_operation_type"),
        table_name="operational_incidents",
    )
    op.drop_index(
        op.f("ix_operational_incidents_actor_id"),
        table_name="operational_incidents",
    )
    op.drop_index(
        op.f("ix_operational_incidents_occurred_at"),
        table_name="operational_incidents",
    )
    op.drop_table("operational_incidents")

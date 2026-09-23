# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Store account roles and their immutable audit history in PostgreSQL."""

import sqlalchemy as sa
from alembic import op

revision = "engine_0004"
down_revision = "engine_0003"


def upgrade() -> None:
    op.alter_column("accounts", "name", existing_type=sa.String(), nullable=True)
    op.alter_column("accounts", "avatar", existing_type=sa.String(), nullable=True)
    op.add_column("accounts", sa.Column("role", sa.String(), nullable=True))
    op.add_column(
        "accounts", sa.Column("access_granted_by", sa.String(), nullable=True)
    )
    op.add_column(
        "accounts",
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_accounts_role",
        "accounts",
        "role IS NULL OR role IN ('owner','admin','user')",
    )
    op.create_check_constraint(
        "ck_accounts_access_grant",
        "accounts",
        "(role = 'user' AND access_granted_by IS NOT NULL "
        "AND access_granted_at IS NOT NULL) OR "
        "(role IS DISTINCT FROM 'user' AND access_granted_by IS NULL "
        "AND access_granted_at IS NULL)",
    )
    op.create_index("ix_accounts_access_granted_by", "accounts", ["access_granted_by"])
    op.create_table(
        "access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("discord_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_events_actor_id", "access_events", ["actor_id"])
    op.create_index("ix_access_events_discord_id", "access_events", ["discord_id"])
    op.create_index(
        "ix_access_events_occurred_at", "access_events", ["occurred_at", "id"]
    )


def downgrade() -> None:
    raise RuntimeError("Access audit history is intentionally irreversible.")

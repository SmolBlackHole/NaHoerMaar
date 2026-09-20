# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord accounts and sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column("requests", sa.Column("actor_id", sa.Uuid()))
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("discord_id", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("avatar", sa.String(), nullable=False),
        sa.Column("profile_complete", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_sessions_account_id", "sessions", ["account_id"])
    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column("browser_hash", sa.String(), nullable=False, unique=True),
        sa.Column("verifier", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("sessions")
    op.drop_table("accounts")
    op.drop_column("requests", "actor_id")

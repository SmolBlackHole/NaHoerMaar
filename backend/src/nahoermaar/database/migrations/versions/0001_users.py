# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Initial User aggregate schema."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_users"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "admin",
                "user",
                name="access_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("access_granted_by", sa.Uuid(), nullable=True),
        sa.Column("access_granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(role = 'user' AND access_granted_by IS NOT NULL "
            "AND access_granted_at IS NOT NULL) OR "
            "(role IS DISTINCT FROM 'user' AND access_granted_by IS NULL "
            "AND access_granted_at IS NULL)",
            name="ck_users_access_grant",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_users_update_not_before_creation",
        ),
        sa.CheckConstraint(
            "last_login_at IS NULL OR last_login_at >= created_at",
            name="ck_users_login_not_before_creation",
        ),
        sa.ForeignKeyConstraint(
            ["access_granted_by"],
            ["users.id"],
            name="fk_users_access_granted_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_access_granted_by", "users", ["access_granted_by"])
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "discord_identities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("discord_id", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("avatar_hash", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "discord_id ~ '^[1-9][0-9]{0,19}$' "
            "AND discord_id::numeric < 18446744073709551616",
            name="ck_discord_identities_valid_snowflake",
        ),
        sa.CheckConstraint(
            "(username IS NULL AND avatar_hash IS NULL AND synced_at IS NULL) OR "
            "(username IS NOT NULL AND synced_at IS NOT NULL)",
            name="ck_discord_identities_metadata_complete",
        ),
        sa.CheckConstraint(
            "username IS NULL OR "
            "(char_length(username) BETWEEN 1 AND 32 "
            "AND username = btrim(username))",
            name="ck_discord_identities_username_valid",
        ),
        sa.CheckConstraint(
            "avatar_hash IS NULL OR "
            "(char_length(avatar_hash) BETWEEN 1 AND 64 "
            "AND avatar_hash = btrim(avatar_hash))",
            name="ck_discord_identities_avatar_hash_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_discord_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_discord_identities"),
        sa.UniqueConstraint(
            "discord_id",
            name="uq_discord_identities_discord_id",
        ),
    )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=32), nullable=True),
        sa.Column("pixabot", sa.String(length=4), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "display_name IS NULL OR "
            "(char_length(display_name) BETWEEN 1 AND 32 "
            "AND display_name = btrim(display_name))",
            name="ck_user_profiles_display_name_valid",
        ),
        sa.CheckConstraint(
            "pixabot IS NULL OR pixabot ~ '^[0-9a-f]{4}$'",
            name="ck_user_profiles_pixabot_valid",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_profiles_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_profiles"),
    )

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "light",
                "dark",
                "system",
                "time",
                name="appearance_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("artwork_colors", sa.Boolean(), nullable=False),
        sa.Column(
            "primary_color",
            sa.Enum(
                "red",
                "orange",
                "amber",
                "yellow",
                "lime",
                "green",
                "emerald",
                "teal",
                "cyan",
                "sky",
                "blue",
                "indigo",
                "violet",
                "purple",
                "fuchsia",
                "pink",
                "rose",
                name="primary_color",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "neutral_color",
            sa.Enum(
                "slate",
                "gray",
                "zinc",
                "neutral",
                "stone",
                name="neutral_color",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "font_family",
            sa.Enum(
                "Public Sans",
                "DM Sans",
                "Geist",
                "Inter",
                "Poppins",
                "Outfit",
                "Raleway",
                name="font_family",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "icon_set",
            sa.Enum(
                "lucide",
                "ph",
                "heroicons",
                "tabler",
                name="icon_set",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "text_size",
            sa.Enum(
                "sm",
                "md",
                "lg",
                name="text_size",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_preferences_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_preferences"),
    )

    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("browser_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("verifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_login_attempts_positive_lifetime",
        ),
        sa.PrimaryKeyConstraint("state_hash", name="pk_login_attempts"),
        sa.UniqueConstraint("browser_hash", name="uq_login_attempts_browser_hash"),
    )
    op.create_index("ix_login_attempts_expires_at", "login_attempts", ["expires_at"])

    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_browser_sessions_positive_lifetime",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_browser_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_browser_sessions"),
    )
    op.create_index("ix_browser_sessions_user_id", "browser_sessions", ["user_id"])
    op.create_index(
        "ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"]
    )

    op.create_table(
        "access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "granted",
                "revoked",
                "operator_role_changed",
                name="access_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "role_before",
            sa.Enum(
                "owner",
                "admin",
                "user",
                name="access_role_before",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "role_after",
            sa.Enum(
                "owner",
                "admin",
                "user",
                name="access_role_after",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role_before IS DISTINCT FROM role_after",
            name="ck_access_events_role_transition",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name="fk_access_events_subject_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_access_events_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_access_events"),
    )
    op.create_index(
        "ix_access_events_subject_user_id", "access_events", ["subject_user_id"]
    )
    op.create_index(
        "ix_access_events_actor_user_id", "access_events", ["actor_user_id"]
    )
    op.create_index("ix_access_events_occurred_at", "access_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_access_events_occurred_at", table_name="access_events")
    op.drop_index("ix_access_events_actor_user_id", table_name="access_events")
    op.drop_index("ix_access_events_subject_user_id", table_name="access_events")
    op.drop_table("access_events")
    op.drop_index("ix_browser_sessions_expires_at", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_index("ix_login_attempts_expires_at", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_table("user_preferences")
    op.drop_table("user_profiles")
    op.drop_table("discord_identities")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_access_granted_by", table_name="users")
    op.drop_table("users")

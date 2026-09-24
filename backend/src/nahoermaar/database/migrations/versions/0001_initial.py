# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Initial NaHörMaar application schema."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
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

    op.create_table(
        "artists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) > 0",
            name=op.f("ck_artists_name_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artists")),
    )
    op.create_table(
        "discovery_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "search",
                "playlist",
                name="discovery_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("locator", sa.String(length=500), nullable=False),
        sa.Column("result_limit", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "locator = btrim(locator) AND char_length(locator) > 0",
            name=op.f("ck_discovery_keys_locator_valid"),
        ),
        sa.CheckConstraint(
            "result_limit BETWEEN 1 AND 100", name=op.f("ck_discovery_keys_limit_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_keys")),
        sa.UniqueConstraint(
            "kind",
            "provider_key",
            "locator",
            "result_limit",
            name="uq_discovery_keys_identity",
        ),
    )
    op.create_index(
        op.f("ix_discovery_keys_last_requested_at"),
        "discovery_keys",
        ["last_requested_at"],
        unique=False,
    )
    op.create_table(
        "tracks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("artwork_url", sa.Text(), nullable=True),
        sa.Column("album_title", sa.String(length=500), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("isrc", sa.String(length=15), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name=op.f("ck_tracks_duration_positive"),
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND char_length(title) > 0",
            name=op.f("ck_tracks_title_valid"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_tracks_update_not_before_creation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracks")),
        sa.UniqueConstraint("isrc", name="uq_tracks_isrc"),
    )
    op.create_index(op.f("ix_tracks_isrc"), "tracks", ["isrc"], unique=False)
    op.create_table(
        "artist_sources",
        sa.Column("artist_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "youtube",
                "youtube_music",
                name="catalog_provider",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("observed_name", sa.String(length=200), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "checked_at >= first_seen_at",
            name=op.f("ck_artist_sources_check_not_before_discovery"),
        ),
        sa.CheckConstraint(
            "external_id = btrim(external_id) AND char_length(external_id) > 0",
            name=op.f("ck_artist_sources_external_id_valid"),
        ),
        sa.CheckConstraint(
            "observed_name = btrim(observed_name) AND char_length(observed_name) > 0",
            name=op.f("ck_artist_sources_observed_name_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_artist_sources_artist_id_artists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "artist_id", "provider", name=op.f("pk_artist_sources")
        ),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_artist_sources_provider_external_id"
        ),
    )
    op.create_index(
        op.f("ix_artist_sources_checked_at"),
        "artist_sources",
        ["checked_at"],
        unique=False,
    )
    op.create_table(
        "discovery_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("playlist_title", sa.String(length=500), nullable=True),
        sa.Column("source_has_more", sa.Boolean(), nullable=False),
        sa.Column("continuation", sa.String(length=4096), nullable=True),
        sa.CheckConstraint(
            "expires_at > fetched_at",
            name=op.f("ck_discovery_snapshots_positive_lifetime"),
        ),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["discovery_keys.id"],
            name=op.f("fk_discovery_snapshots_key_id_discovery_keys"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_snapshots")),
    )
    op.create_index(
        op.f("ix_discovery_snapshots_expires_at"),
        "discovery_snapshots",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_discovery_snapshots_fetched_at"),
        "discovery_snapshots",
        ["fetched_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_discovery_snapshots_key_id"),
        "discovery_snapshots",
        ["key_id"],
        unique=False,
    )
    op.create_table(
        "track_artists",
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_track_artists_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_track_artists_artist_id_artists"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_artists_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("track_id", "position", name=op.f("pk_track_artists")),
        sa.UniqueConstraint(
            "track_id", "artist_id", name="uq_track_artists_track_artist"
        ),
    )
    op.create_index(
        op.f("ix_track_artists_artist_id"), "track_artists", ["artist_id"], unique=False
    )
    op.create_table(
        "track_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "youtube",
                "youtube_music",
                name="catalog_provider",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_title", sa.String(length=500), nullable=False),
        sa.Column("observed_artist", sa.String(length=500), nullable=True),
        sa.Column("observed_duration_seconds", sa.Float(), nullable=True),
        sa.Column("observed_artwork_url", sa.Text(), nullable=True),
        sa.Column("observed_album_title", sa.String(length=500), nullable=True),
        sa.Column("observed_release_date", sa.Date(), nullable=True),
        sa.Column("observed_isrc", sa.String(length=15), nullable=True),
        sa.Column("uploader_name", sa.String(length=200), nullable=True),
        sa.Column("uploader_url", sa.Text(), nullable=True),
        sa.Column(
            "quality",
            sa.Enum(
                "discovery",
                "detail",
                name="observation_quality",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "availability",
            sa.Enum(
                "unknown",
                "available",
                "unavailable",
                name="source_availability",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "checked_at >= first_seen_at",
            name=op.f("ck_track_sources_check_not_before_discovery"),
        ),
        sa.CheckConstraint(
            "external_id = btrim(external_id) AND char_length(external_id) > 0",
            name=op.f("ck_track_sources_external_id_valid"),
        ),
        sa.CheckConstraint(
            "observed_duration_seconds IS NULL OR observed_duration_seconds > 0",
            name=op.f("ck_track_sources_observed_duration_positive"),
        ),
        sa.CheckConstraint(
            "observed_title = btrim(observed_title) AND char_length(observed_title) > 0",
            name=op.f("ck_track_sources_observed_title_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_sources_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_sources")),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_track_sources_provider_external_id"
        ),
    )
    op.create_index(
        op.f("ix_track_sources_checked_at"),
        "track_sources",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_track_sources_track_id"), "track_sources", ["track_id"], unique=False
    )
    op.create_table(
        "discovery_results",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("track_source_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_discovery_results_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["discovery_snapshots.id"],
            name=op.f("fk_discovery_results_snapshot_id_discovery_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_source_id"],
            ["track_sources.id"],
            name=op.f("fk_discovery_results_track_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "position", name=op.f("pk_discovery_results")
        ),
    )
    op.create_index(
        op.f("ix_discovery_results_track_source_id"),
        "discovery_results",
        ["track_source_id"],
        unique=False,
    )
    op.create_table(
        "track_source_artists",
        sa.Column("track_source_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_track_source_artists_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_track_source_artists_artist_id_artists"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_source_id"],
            ["track_sources.id"],
            name=op.f("fk_track_source_artists_track_source_id_track_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "track_source_id", "position", name=op.f("pk_track_source_artists")
        ),
        sa.UniqueConstraint(
            "track_source_id", "artist_id", name="uq_track_source_artists_source_artist"
        ),
    )
    op.create_index(
        op.f("ix_track_source_artists_artist_id"),
        "track_source_artists",
        ["artist_id"],
        unique=False,
    )
    op.create_table(
        "listening_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("queue_revision", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("crossfade_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "crossfade_seconds IN (0, 3, 4, 5, 6, 7)",
            name=op.f("ck_listening_sessions_crossfade_supported"),
        ),
        sa.CheckConstraint(
            "queue_revision >= 0 AND queue_revision <= revision",
            name=op.f("ck_listening_sessions_queue_revision_valid"),
        ),
        sa.CheckConstraint(
            "revision >= 0", name=op.f("ck_listening_sessions_revision_non_negative")
        ),
        sa.CheckConstraint(
            "volume >= 0 AND volume <= 1",
            name=op.f("ck_listening_sessions_volume_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listening_sessions")),
        sa.UniqueConstraint(
            "session_key", name=op.f("uq_listening_sessions_session_key")
        ),
    )
    op.create_table(
        "operation_receipts",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("command_type", sa.String(length=100), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "queue.added",
                "queue.removed",
                "queue.moved",
                "queue.cleared",
                "queue.restored",
                "radio.started",
                "radio.stopped",
                "radio.retried",
                "radio.filled",
                "radio.failed",
                "playback.updated",
                name="player_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("added_count", sa.Integer(), nullable=False),
        sa.Column("removed_count", sa.Integer(), nullable=False),
        sa.Column("restored_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("undo_id", sa.Uuid(), nullable=True),
        sa.Column("undo_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_operation_receipts_positive_lifetime"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_operation_receipts_session_id_listening_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("operation_id", name=op.f("pk_operation_receipts")),
    )
    op.create_index(
        op.f("ix_operation_receipts_expires_at"),
        "operation_receipts",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operation_receipts_session_id"),
        "operation_receipts",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "queue_undos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_queue_undos_positive_lifetime")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_queue_undos_actor_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_queue_undos_session_id_listening_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue_undos")),
    )
    op.create_index(
        op.f("ix_queue_undos_actor_id"), "queue_undos", ["actor_id"], unique=False
    )
    op.create_index(
        op.f("ix_queue_undos_expires_at"), "queue_undos", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_queue_undos_session_id"), "queue_undos", ["session_id"], unique=False
    )
    op.create_table(
        "operation_receipt_entries",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operation_receipts.operation_id"],
            name=op.f("fk_operation_receipt_entries_operation_id_operation_receipts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "operation_id", "position", name=op.f("pk_operation_receipt_entries")
        ),
    )
    op.create_table(
        "queue_undo_groups",
        sa.Column("undo_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("previous_entry_id", sa.Uuid(), nullable=True),
        sa.Column("next_entry_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["undo_id"],
            ["queue_undos.id"],
            name=op.f("fk_queue_undo_groups_undo_id_queue_undos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "undo_id", "position", name=op.f("pk_queue_undo_groups")
        ),
    )
    op.create_table(
        "radio_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "seed_kind",
            sa.Enum(
                "track",
                "playlist",
                name="player_media_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("seed_track_source_id", sa.Uuid(), nullable=True),
        sa.Column("seed_discovery_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("initiated_by", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "active",
                "loading",
                "waiting",
                name="radio_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("continuation", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(seed_kind = 'track' AND seed_track_source_id IS NOT NULL AND seed_discovery_snapshot_id IS NULL) OR (seed_kind = 'playlist' AND seed_track_source_id IS NULL AND seed_discovery_snapshot_id IS NOT NULL)",
            name=op.f("ck_radio_runs_seed_identity_valid"),
        ),
        sa.CheckConstraint(
            "(state = 'loading' AND request_id IS NOT NULL) OR (state <> 'loading' AND request_id IS NULL)",
            name=op.f("ck_radio_runs_request_state_valid"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_radio_runs_end_not_before_start"),
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by"],
            ["users.id"],
            name=op.f("fk_radio_runs_initiated_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seed_discovery_snapshot_id"],
            ["discovery_snapshots.id"],
            name=op.f("fk_radio_runs_seed_discovery_snapshot_id_discovery_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seed_track_source_id"],
            ["track_sources.id"],
            name=op.f("fk_radio_runs_seed_track_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_radio_runs_session_id_listening_sessions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radio_runs")),
    )
    op.create_index(
        op.f("ix_radio_runs_initiated_by"), "radio_runs", ["initiated_by"], unique=False
    )
    op.create_index(
        op.f("ix_radio_runs_session_id"), "radio_runs", ["session_id"], unique=False
    )
    op.create_index(
        "uq_radio_runs_active_session",
        "radio_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "radio_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_radio_candidates_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["radio_runs.id"],
            name=op.f("fk_radio_candidates_run_id_radio_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["track_sources.id"],
            name=op.f("fk_radio_candidates_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_radio_candidates_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radio_candidates")),
        sa.UniqueConstraint("run_id", "position", name="uq_radio_candidates_position"),
        sa.UniqueConstraint("run_id", "track_id", name="uq_radio_candidates_track"),
    )
    op.create_index(
        op.f("ix_radio_candidates_run_id"), "radio_candidates", ["run_id"], unique=False
    )
    op.create_table(
        "radio_exclusions",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["radio_runs.id"],
            name=op.f("fk_radio_exclusions_run_id_radio_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_radio_exclusions_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "track_id", name=op.f("pk_radio_exclusions")),
    )
    op.create_table(
        "track_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "origin",
            sa.Enum(
                "manual",
                "radio",
                name="request_origin",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("radio_run_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(origin = 'manual' AND requested_by IS NOT NULL AND radio_run_id IS NULL) OR (origin = 'radio' AND requested_by IS NULL AND radio_run_id IS NOT NULL)",
            name=op.f("ck_track_requests_origin_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["radio_run_id"],
            ["radio_runs.id"],
            name=op.f("fk_track_requests_radio_run_id_radio_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_track_requests_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_track_requests_session_id_listening_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["track_sources.id"],
            name=op.f("fk_track_requests_source_id_track_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_requests_track_id_tracks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_requests")),
    )
    op.create_index(
        op.f("ix_track_requests_radio_run_id"),
        "track_requests",
        ["radio_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_track_requests_requested_by"),
        "track_requests",
        ["requested_by"],
        unique=False,
    )
    op.create_index(
        "ix_track_requests_session_requested",
        "track_requests",
        ["session_id", "requested_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_track_requests_source_id"),
        "track_requests",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_track_requests_track_id"), "track_requests", ["track_id"], unique=False
    )
    op.create_table(
        "player_checkpoints",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "intent",
            sa.Enum(
                "stopped",
                "playing",
                "paused",
                name="playback_intent",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "(intent = 'stopped' AND request_id IS NULL AND position_seconds = 0) OR (intent <> 'stopped' AND request_id IS NOT NULL)",
            name=op.f("ck_player_checkpoints_intent_request_valid"),
        ),
        sa.CheckConstraint(
            "position_seconds >= 0",
            name=op.f("ck_player_checkpoints_position_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["track_requests.id"],
            name=op.f("fk_player_checkpoints_request_id_track_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_player_checkpoints_session_id_listening_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_player_checkpoints")),
    )
    op.create_table(
        "queue_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_queue_entries_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["track_requests.id"],
            name=op.f("fk_queue_entries_request_id_track_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["listening_sessions.id"],
            name=op.f("fk_queue_entries_session_id_listening_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue_entries")),
        sa.UniqueConstraint("request_id", name=op.f("uq_queue_entries_request_id")),
        sa.UniqueConstraint("session_id", "position", name="uq_queue_entries_position"),
    )
    op.create_index(
        op.f("ix_queue_entries_session_id"),
        "queue_entries",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "queue_undo_entries",
        sa.Column("undo_id", sa.Uuid(), nullable=False),
        sa.Column("group_position", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["track_requests.id"],
            name=op.f("fk_queue_undo_entries_request_id_track_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["undo_id", "group_position"],
            ["queue_undo_groups.undo_id", "queue_undo_groups.position"],
            name="fk_queue_undo_entries_group_queue_undo_groups",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "undo_id", "group_position", "position", name=op.f("pk_queue_undo_entries")
        ),
    )
    op.create_index(
        op.f("ix_queue_undo_entries_request_id"),
        "queue_undo_entries",
        ["request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_queue_undo_entries_request_id"), table_name="queue_undo_entries"
    )
    op.drop_table("queue_undo_entries")
    op.drop_index(op.f("ix_queue_entries_session_id"), table_name="queue_entries")
    op.drop_table("queue_entries")
    op.drop_table("player_checkpoints")
    op.drop_index(op.f("ix_track_requests_track_id"), table_name="track_requests")
    op.drop_index(op.f("ix_track_requests_source_id"), table_name="track_requests")
    op.drop_index("ix_track_requests_session_requested", table_name="track_requests")
    op.drop_index(op.f("ix_track_requests_requested_by"), table_name="track_requests")
    op.drop_index(op.f("ix_track_requests_radio_run_id"), table_name="track_requests")
    op.drop_table("track_requests")
    op.drop_table("radio_exclusions")
    op.drop_index(op.f("ix_radio_candidates_run_id"), table_name="radio_candidates")
    op.drop_table("radio_candidates")
    op.drop_index(
        "uq_radio_runs_active_session",
        table_name="radio_runs",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_index(op.f("ix_radio_runs_session_id"), table_name="radio_runs")
    op.drop_index(op.f("ix_radio_runs_initiated_by"), table_name="radio_runs")
    op.drop_table("radio_runs")
    op.drop_table("queue_undo_groups")
    op.drop_table("operation_receipt_entries")
    op.drop_index(op.f("ix_queue_undos_session_id"), table_name="queue_undos")
    op.drop_index(op.f("ix_queue_undos_expires_at"), table_name="queue_undos")
    op.drop_index(op.f("ix_queue_undos_actor_id"), table_name="queue_undos")
    op.drop_table("queue_undos")
    op.drop_index(
        op.f("ix_operation_receipts_session_id"), table_name="operation_receipts"
    )
    op.drop_index(
        op.f("ix_operation_receipts_expires_at"), table_name="operation_receipts"
    )
    op.drop_table("operation_receipts")
    op.drop_table("listening_sessions")
    op.drop_index(
        op.f("ix_track_source_artists_artist_id"), table_name="track_source_artists"
    )
    op.drop_table("track_source_artists")
    op.drop_index(
        op.f("ix_discovery_results_track_source_id"), table_name="discovery_results"
    )
    op.drop_table("discovery_results")
    op.drop_index(op.f("ix_track_sources_track_id"), table_name="track_sources")
    op.drop_index(op.f("ix_track_sources_checked_at"), table_name="track_sources")
    op.drop_table("track_sources")
    op.drop_index(op.f("ix_track_artists_artist_id"), table_name="track_artists")
    op.drop_table("track_artists")
    op.drop_index(
        op.f("ix_discovery_snapshots_key_id"), table_name="discovery_snapshots"
    )
    op.drop_index(
        op.f("ix_discovery_snapshots_fetched_at"), table_name="discovery_snapshots"
    )
    op.drop_index(
        op.f("ix_discovery_snapshots_expires_at"), table_name="discovery_snapshots"
    )
    op.drop_table("discovery_snapshots")
    op.drop_index(op.f("ix_artist_sources_checked_at"), table_name="artist_sources")
    op.drop_table("artist_sources")
    op.drop_index(op.f("ix_tracks_isrc"), table_name="tracks")
    op.drop_table("tracks")
    op.drop_index(
        op.f("ix_discovery_keys_last_requested_at"), table_name="discovery_keys"
    )
    op.drop_table("discovery_keys")
    op.drop_table("artists")

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

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nahoermaar.users.domain import (
    AccessAction,
    AccessRole,
    Appearance,
    AppearanceMode,
    DiscordIdentity,
    FontFamily,
    IconSet,
    NeutralColor,
    PrimaryColor,
    TextSize,
    User,
    UserId,
    UserProfile,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def test_access_terms_are_typed_and_privileged_roles_are_explicit() -> None:
    assert AccessRole.OWNER.privileged
    assert AccessRole.ADMIN.privileged
    assert not AccessRole.USER.privileged
    assert AccessAction.OPERATOR_ROLE_CHANGED.value == "operator_role_changed"


def test_appearance_uses_the_existing_defaults_as_domain_values() -> None:
    appearance = Appearance()

    assert appearance == Appearance(
        mode=AppearanceMode.DARK,
        artwork_colors=True,
        primary_color=PrimaryColor.TEAL,
        neutral_color=NeutralColor.ZINC,
        font_family=FontFamily.GEIST,
        icon_set=IconSet.LUCIDE,
        text_size=TextSize.MEDIUM,
    )


def test_pregranted_discord_identity_can_remain_unsynchronized() -> None:
    operator_id = UserId(uuid4())
    user = User(
        id=UserId(uuid4()),
        discord=DiscordIdentity("1377708476259897478"),
        created_at=NOW,
        updated_at=NOW,
        role=AccessRole.USER,
        access_granted_by=operator_id,
        access_granted_at=NOW,
    )

    assert user.has_access
    assert not user.discord.complete
    assert not user.profile_complete


def test_discord_and_local_profiles_remain_independent() -> None:
    user = User(
        id=UserId(uuid4()),
        discord=DiscordIdentity(
            "1377708476259897478",
            username="discord-name",
            avatar_hash="a_012345",
            synced_at=NOW,
        ),
        profile=UserProfile("Local name", "021a"),
        created_at=NOW,
        updated_at=NOW,
    )

    assert user.discord.username == "discord-name"
    assert user.profile.display_name == "Local name"
    assert user.discord.complete
    assert user.profile_complete


@pytest.mark.parametrize(
    "discord_id",
    ["", "0", "-1", "01", "not-a-snowflake", str(2**64)],
)
def test_discord_identity_rejects_invalid_snowflakes(discord_id: str) -> None:
    with pytest.raises(ValueError, match="snowflake"):
        DiscordIdentity(discord_id)


def test_unsynchronized_discord_identity_rejects_partial_metadata() -> None:
    with pytest.raises(ValueError, match="cannot contain metadata"):
        DiscordIdentity("1377708476259897478", avatar_hash="avatar")


@pytest.mark.parametrize(
    ("display_name", "pixabot"),
    [
        (" padded", None),
        ("", None),
        (None, "XYZ1"),
        (None, "123"),
    ],
)
def test_local_profile_rejects_values_that_cannot_be_persisted(
    display_name: str | None,
    pixabot: str | None,
) -> None:
    with pytest.raises(ValueError):
        UserProfile(display_name, pixabot)


@pytest.mark.parametrize(
    ("role", "actor", "granted_at", "message"),
    [
        (AccessRole.USER, None, None, "requires"),
        (None, UserId(uuid4()), NOW, "Only the user role"),
        (AccessRole.ADMIN, UserId(uuid4()), NOW, "Only the user role"),
        (AccessRole.USER, UserId(uuid4()), None, "together"),
    ],
)
def test_user_rejects_inconsistent_access_state(
    role: AccessRole | None,
    actor: UserId | None,
    granted_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        User(
            id=UserId(uuid4()),
            discord=DiscordIdentity("1377708476259897478"),
            created_at=NOW,
            updated_at=NOW,
            role=role,
            access_granted_by=actor,
            access_granted_at=granted_at,
        )


def test_user_rejects_naive_or_reversed_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        User(
            id=UserId(uuid4()),
            discord=DiscordIdentity("1377708476259897478"),
            created_at=datetime(2026, 9, 24, 12),
            updated_at=NOW,
        )

    with pytest.raises(ValueError, match="cannot precede"):
        User(
            id=UserId(uuid4()),
            discord=DiscordIdentity("1377708476259897478"),
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )

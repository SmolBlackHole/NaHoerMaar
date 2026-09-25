# SPDX-FileCopyrightText: 2026 SmolBlackHole
# SPDX-License-Identifier: MPL-2.0

"""Discord snowflakes cross the JSON boundary without JavaScript rounding."""

import json
from uuid import uuid4

from pydantic import ValidationError
import pytest

from nahoermaar.api.player import JoinVoiceInput, VoiceChannelView


def test_voice_channel_id_round_trip() -> None:
    channel_id = "1550894913980465212"
    channel = VoiceChannelView(
        id=channel_id,
        name="General",
        guild_id="1550894913980465213",
        guild_name="Friends",
        can_connect=True,
        can_speak=True,
    )
    payload = json.loads(channel.model_dump_json())
    assert payload["id"] == channel_id
    assert payload["guild_id"] == "1550894913980465213"
    command = JoinVoiceInput(operation_id=uuid4(), channel_id=payload["id"])
    assert int(command.channel_id) == 1550894913980465212


@pytest.mark.parametrize("value", [1550894913980465212, "0", "-1", "1.5", "abc"])
def test_join_requires_a_positive_decimal_string(value: object) -> None:
    with pytest.raises(ValidationError):
        JoinVoiceInput.model_validate(
            {"operation_id": str(uuid4()), "channel_id": value}
        )

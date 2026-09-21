# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import discord
import pytest

from nahormaar_backend.engine.commands import DiscordCommands
from nahormaar_backend.engine.domain.queue import Add
from .test_engine_api import DISCORD_ID, fixture
from .test_radio_session import until


def interaction() -> Mock:
    channel = Mock(spec=discord.VoiceChannel, id=123)
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, connect=True, speak=True
    )
    member = Mock(
        spec=discord.Member, id=int(DISCORD_ID), voice=SimpleNamespace(channel=channel)
    )
    return Mock(
        spec=discord.Interaction,
        id=999,
        user=member,
        guild=Mock(spec=discord.Guild),
        response=Mock(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "whitelist",
        "no_voice",
        "stage",
        "view_channel",
        "connect",
        "speak",
        "transport",
    ],
)
def test_summon_uses_real_session_and_only_reports_success_after_join(
    tmp_path: Path, failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (_http, services, provider, audio):
            client = discord.Client(intents=discord.Intents.none())
            tree = DiscordCommands(
                client, services.session, services.auth.settings.access_path
            )
            track = await services.catalog.track(provider.finding.reference.source_url)
            await services.session.request(uuid4(), Add((track.id,)))
            event = interaction()
            if failure == "whitelist":
                event.user.id = 123
            elif failure == "no_voice":
                event.user.voice = None
            elif failure == "stage":
                event.user.voice.channel = Mock(spec=discord.StageChannel)
            elif failure in {"view_channel", "connect", "speak"}:
                setattr(
                    event.user.voice.channel.permissions_for.return_value,
                    failure,
                    False,
                )
            elif failure == "transport":
                monkeypatch.setattr(
                    services.voice,
                    "connect",
                    AsyncMock(side_effect=RuntimeError("private detail")),
                )
            try:
                await tree.summon(cast(discord.Interaction[discord.Client], event))
                message = event.edit_original_response.call_args.kwargs["content"]
                assert "private detail" not in message
                if failure is None:
                    assert message == ":3" and services.voice.connection
                    await until(lambda: audio.progress is not None)
                    audio.confirm()
                    await until(lambda: len(services.session.snapshot.history) == 1)
                    assert services.session.snapshot.checkpoint.track_id == track.id
                    assert services.session.snapshot.queue.entries == ()
                    await tree.summon(cast(discord.Interaction[discord.Client], event))
                    assert (
                        len(audio.started) == 1
                        and len(services.session.snapshot.history) == 1
                    )
                else:
                    assert (
                        message != ":3"
                        and len(services.session.snapshot.queue.entries) == 1
                    )
                    assert not audio.started
            finally:
                await client.close()

    asyncio.run(scenario())

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from nahormaar_backend.application.audio import VoiceError
from nahormaar_backend.domain.models import PlaybackState, QueueEntry
from nahormaar_backend.integrations.discord_commands import DiscordCommands
from nahormaar_backend.integrations.discord_voice import DiscordVoice
from test_playback import _controller, _wait_until  # pyright: ignore[reportPrivateUsage]


def interaction(channel_id: int = 7) -> Mock:
    channel = Mock(spec=discord.VoiceChannel, id=channel_id, mention=f"<#{channel_id}>")
    channel.permissions_for.return_value = discord.Permissions(
        view_channel=True, connect=True, speak=True
    )
    member = Mock(spec=discord.Member, id=42, voice=SimpleNamespace(channel=channel))
    return Mock(
        spec=discord.Interaction,
        user=member,
        guild=Mock(spec=discord.Guild),
        response=Mock(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.fixture
def command(tmp_path: Path) -> tuple[DiscordCommands, AsyncMock, Path]:
    path = tmp_path / "access.toml"
    path.write_text('discord_ids = ["42"]', encoding="utf-8")
    connect = AsyncMock()
    client = discord.Client(intents=discord.Intents.none())
    return DiscordCommands(client, path, connect), connect, path


def test_command_registration_is_guild_only_and_has_no_target_argument(
    command: tuple[DiscordCommands, AsyncMock, Path],
) -> None:
    tree, _, _ = command
    entry = tree.get_command("pspsps")
    assert isinstance(entry, discord.app_commands.Command)
    assert entry.guild_only
    assert entry.allowed_installs is not None
    assert entry.allowed_installs.to_array() == [0]
    assert entry.parameters == []


def test_whitelisted_user_can_summon_without_a_dashboard_account(
    command: tuple[DiscordCommands, AsyncMock, Path],
) -> None:
    tree, connect, _ = command
    event = interaction()
    asyncio.run(tree.summon(cast(discord.Interaction[discord.Client], event)))
    event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    connect.assert_awaited_once_with(7)
    assert event.edit_original_response.call_args.kwargs["content"] == ":3"


@pytest.mark.parametrize("contents", ["discord_ids = []", None, "broken ["])
def test_revocation_and_unreadable_whitelist_block_subsequent_commands(
    command: tuple[DiscordCommands, AsyncMock, Path], contents: str | None
) -> None:
    tree, connect, path = command

    async def scenario() -> None:
        await tree.summon(cast(discord.Interaction[discord.Client], interaction()))
        if contents is None:
            path.unlink()
        else:
            path.write_text(contents, encoding="utf-8")
        event = interaction()
        await tree.summon(cast(discord.Interaction[discord.Client], event))
        connect.assert_awaited_once_with(7)
        assert "whitelist" in event.edit_original_response.call_args.kwargs["content"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["dm", "no_voice", "stage", "view_channel", "connect", "speak"]
)
def test_unavailable_channel_does_not_touch_playback(
    command: tuple[DiscordCommands, AsyncMock, Path], case: str
) -> None:
    tree, connect, _ = command
    event = interaction()
    if case == "dm":
        event.guild = None
    elif case == "no_voice":
        event.user.voice = None
    elif case == "stage":
        event.user.voice.channel = Mock(spec=discord.StageChannel)
    else:
        permissions = event.user.voice.channel.permissions_for.return_value
        setattr(permissions, case, False)
    asyncio.run(tree.summon(cast(discord.Interaction[discord.Client], event)))
    connect.assert_not_awaited()
    event.edit_original_response.assert_awaited_once()


@pytest.mark.parametrize(
    "error", [VoiceError("private detail"), RuntimeError("private detail")]
)
def test_join_failures_have_a_private_response_without_internal_details(
    command: tuple[DiscordCommands, AsyncMock, Path], error: Exception
) -> None:
    tree, connect, _ = command
    connect.side_effect = error
    event = interaction()
    asyncio.run(tree.summon(cast(discord.Interaction[discord.Client], event)))
    message = event.edit_original_response.call_args.kwargs["content"]
    assert "private detail" not in message
    assert "Bin bei dir" not in message


@pytest.mark.parametrize("error", [None, discord.ClientException("unavailable")])
def test_command_sync_failure_does_not_prevent_voice_startup(
    command: tuple[DiscordCommands, AsyncMock, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception | None,
) -> None:
    tree, _, _ = command
    sync = AsyncMock(side_effect=error)
    monkeypatch.setattr(tree, "sync", sync)
    asyncio.run(tree.register())
    sync.assert_awaited_once_with()
    assert ("/pspsps may be unavailable" in caplog.text) is (error is not None)


def test_startup_registers_command_but_ready_reconnect_does_not_resync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register = AsyncMock()
    monkeypatch.setattr(DiscordCommands, "register", register)

    async def scenario() -> None:
        voice = DiscordVoice(tmp_path / "ffmpeg")
        voice.install_commands(tmp_path / "access.toml", AsyncMock())
        client = voice._client  # pyright: ignore[reportPrivateUsage]
        await client.setup_hook()
        await client.on_ready()
        await client.on_ready()
        register.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", [7, 8])
def test_summon_uses_controller_and_preserves_queue(
    tmp_path: Path, target: int
) -> None:
    path = tmp_path / "access.toml"
    path.write_text('discord_ids = ["42"]', encoding="utf-8")

    async def scenario() -> None:
        controller, resolver, voice, _ = await _controller(tmp_path)
        first = QueueEntry("https://youtu.be/first")
        second = QueueEntry("https://youtu.be/second")
        try:
            await controller.enqueue(first)
            await controller.enqueue(second)
            await controller.connect(7)
            await controller.play()
            await _wait_until(lambda: len(resolver.requests) == 1)
            resolver.succeed(0)
            await _wait_until(
                lambda: controller.snapshot.state is PlaybackState.PLAYING
            )
            stopped = voice.stop_count
            tree = DiscordCommands(
                discord.Client(intents=discord.Intents.none()), path, controller.connect
            )
            await tree.summon(
                cast(discord.Interaction[discord.Client], interaction(target))
            )
            assert controller.status.channel_id == target
            if target == 7:
                assert voice.stop_count == stopped
                assert controller.snapshot.current is not None
                assert controller.snapshot.current.id == first.id
            else:
                assert controller.snapshot.state is PlaybackState.IDLE
                assert [entry.id for entry in controller.snapshot.upcoming] == [
                    first.id,
                    second.id,
                ]
                assert len(voice.played) == 1
        finally:
            await controller.close()

    asyncio.run(scenario())

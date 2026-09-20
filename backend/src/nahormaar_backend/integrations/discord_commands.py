# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord slash commands using the same access and player paths as the UI."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import discord
from discord import app_commands

from ..application.access import require_access
from ..application.audio import VoiceError
from ..domain.identity import AuthError

_LOGGER = logging.getLogger(__name__)


class DiscordCommands(app_commands.CommandTree[discord.Client]):
    def __init__(
        self,
        client: discord.Client,
        access_path: Path,
        connect: Callable[[int], Awaitable[object]],
    ) -> None:
        super().__init__(client)
        self._access_path = access_path
        self._connect = connect
        self.add_command(
            app_commands.Command(
                name="pspsps",
                description="Summon the bot to your current voice channel (whitelisted users only).",
                callback=self.summon,
            )
        )

    async def register(self) -> None:
        try:
            async with asyncio.timeout(10):
                await self.sync()
        except (discord.DiscordException, TimeoutError) as error:
            _LOGGER.warning(
                "Discord command registration failed (%s); /pspsps may be unavailable.",
                type(error).__name__,
            )

    @app_commands.guild_only()
    @app_commands.guild_install()
    async def summon(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await asyncio.to_thread(
                require_access, self._access_path, str(interaction.user.id)
            )
        except AuthError as error:
            message = (
                "You are not on the whitelist for this bot."
                if error.code == "access_denied"
                else "The whitelist is currently unavailable. Please try again later."
            )
            await interaction.edit_original_response(content=message)
            return

        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.edit_original_response(
                content="Call /pspsps in a server where the bot is a member."
            )
            return
        voice = member.voice
        if voice is None or voice.channel is None:
            await interaction.edit_original_response(
                content="First join a voice channel and then call /pspsps."
            )
            return
        channel = voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.edit_original_response(
                content="I can only join regular voice channels."
            )
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.connect and permissions.speak):
            await interaction.edit_original_response(
                content="I'm missing permissions in your voice channel."
            )
            return
        try:
            await self._connect(channel.id)
        except VoiceError:
            await interaction.edit_original_response(
                content="I couldn't join your voice channel. Please try again."
            )
            return
        except Exception as error:
            _LOGGER.warning("Discord summon failed (%s).", type(error).__name__)
            await interaction.edit_original_response(
                content="The player is currently unavailable. Please try again later."
            )
            return
        await interaction.edit_original_response(
            content=":3",
            allowed_mentions=discord.AllowedMentions.none(),
        )

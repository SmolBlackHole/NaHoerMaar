# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord entry point into the same Session command path as HTTP."""

import asyncio
import logging
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import discord
from discord import app_commands

from ..application.access import require_access
from ..domain.identity import AuthError
from .domain.playback import Join
from .session import Session

_LOGGER = logging.getLogger(__name__)


class DiscordCommands(app_commands.CommandTree[discord.Client]):
    def __init__(
        self, client: discord.Client, session: Session, access_path: Path
    ) -> None:
        super().__init__(client)
        self.session, self.access_path = session, access_path
        self.add_command(
            app_commands.Command(
                name="pspsps",
                description="Summon the bot to your voice channel.",
                callback=self.summon,
            )
        )

    async def register(self) -> None:
        async with asyncio.timeout(10):
            await self.sync()

    @app_commands.guild_only()
    @app_commands.guild_install()
    async def summon(self, interaction: discord.Interaction[discord.Client]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = ":3"
        try:
            await asyncio.to_thread(
                require_access, self.access_path, str(interaction.user.id)
            )
            member = interaction.user
            if (
                interaction.guild is None
                or not isinstance(member, discord.Member)
                or member.voice is None
                or not isinstance(member.voice.channel, discord.VoiceChannel)
            ):
                message = "Join a voice channel in this server, then call /pspsps."
            else:
                channel = member.voice.channel
                permissions = channel.permissions_for(interaction.guild.me)
                if not (
                    permissions.view_channel
                    and permissions.connect
                    and permissions.speak
                ):
                    message = "I'm missing permissions in your voice channel."
                else:
                    async with self.session.events.subscribe() as changes:
                        result = await self.session.request(
                            uuid5(
                                NAMESPACE_URL, f"discord-interaction:{interaction.id}"
                            ),
                            Join(channel.id),
                        )
                        if result.outcome.code != "ok":
                            raise RuntimeError("Join rejected.")
                        expected = result.snapshot.playback.joining_id
                        async with asyncio.timeout(30):
                            while (
                                self.session.snapshot.playback.connection_id != expected
                                or expected is None
                            ):
                                state = self.session.snapshot
                                if (
                                    expected is None
                                    and state.settings.channel_id == channel.id
                                    and state.playback.connection_id
                                ):
                                    break
                                if (
                                    state.playback.joining_id != expected
                                    or state.playback.error
                                ):
                                    raise RuntimeError("Join failed or superseded.")
                                if await changes.get() is None:
                                    raise RuntimeError("Session closed.")
        except AuthError as error:
            message = (
                "You are not on the whitelist for this bot."
                if error.code == "access_denied"
                else "The whitelist is currently unavailable."
            )
        except Exception as error:
            _LOGGER.warning("engine.discord.summon_failed: %s", type(error).__name__)
            message = "I couldn't join your voice channel. Please try again."
        await interaction.edit_original_response(
            content=message, allowed_mentions=discord.AllowedMentions.none()
        )

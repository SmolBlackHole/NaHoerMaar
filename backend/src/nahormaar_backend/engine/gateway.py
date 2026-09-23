# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord gateway events and profile presentation, not playback decisions."""

import asyncio
import logging
from pathlib import Path

import discord

from ..domain.access import DiscordMember
from .discord import DiscordOutput
from .domain.sessions import PlaybackIntent
from .metadata import MetadataStore
from .session import Session

_LOGGER = logging.getLogger(__name__)
_PRESENCE_INTERVAL_SECONDS = 5.0


class DiscordGateway(discord.Client):
    def __init__(self, ffmpeg_path: Path) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.output = DiscordOutput(self, ffmpeg_path)
        self._sent_activity: dict[str, object] | None = None

    def members(self) -> tuple[DiscordMember, ...]:
        return tuple(
            sorted(
                (
                    DiscordMember(
                        str(member.id),
                        member.name,
                        member.display_name,
                        str(member.display_avatar.url),
                        str(guild.id),
                        guild.name,
                    )
                    for guild in self.guilds
                    for member in guild.members
                    if not member.bot
                ),
                key=lambda item: (
                    item.guild_name.casefold(),
                    item.display_name.casefold(),
                    item.discord_id,
                ),
            )
        )

    async def on_ready(self) -> None:
        self._sent_activity = None
        _LOGGER.info(
            "engine.discord.gateway_ready guilds=%s user=%s",
            len(self.guilds),
            self.user.id if self.user else None,
        )

    async def on_resumed(self) -> None:
        _LOGGER.info("engine.discord.gateway_resumed guilds=%s", len(self.guilds))

    async def on_disconnect(self) -> None:
        _LOGGER.warning("engine.discord.gateway_disconnected")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.output.on_voice_state_update(member, before, after)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        _LOGGER.info("engine.discord.guild_removed guild=%s", guild.id)
        await self.output.on_guild_remove(guild)

    async def update_presence(self, session: Session, metadata: MetadataStore) -> None:
        """Read committed state; coalesce updates and retry without affecting audio."""
        while True:
            try:
                checkpoint = session.snapshot.checkpoint
                track = (
                    await metadata.get(checkpoint.track_id)
                    if checkpoint.track_id
                    else None
                )
                activity: discord.BaseActivity
                if track is None:
                    activity = discord.CustomActivity(name="Bereit für Musik")
                else:
                    title = " ".join((track.metadata.title or "YouTube").split())
                    if checkpoint.intent is PlaybackIntent.PAUSED:
                        activity = discord.CustomActivity(
                            name=f"Pausiert: {title}"[:128]
                        )
                    else:
                        artist = track.metadata.artist or track.metadata.uploader or ""
                        activity = discord.Activity(
                            type=discord.ActivityType.listening,
                            name=title[:128],
                            state=" ".join(artist.split())[:128] or None,
                        )
                value = activity.to_dict()
                if self.is_ready() and value != self._sent_activity:
                    async with asyncio.timeout(10):
                        await self.change_presence(activity=activity)
                    self._sent_activity = value
            except Exception as error:
                _LOGGER.warning(
                    "engine.discord.presence_failed: %s", type(error).__name__
                )
            await asyncio.sleep(_PRESENCE_INTERVAL_SECONDS)

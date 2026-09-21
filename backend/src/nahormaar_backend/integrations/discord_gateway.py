# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Discord client lifetime, profile updates and slash-command registration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import discord

from ..application.audio import ResolvedTrack, VoiceError
from .daily_bio import update_daily_bio
from .discord_commands import DiscordCommands

_PRESENCE_INTERVAL_SECONDS = 5.0
_PRESENCE_TEXT_LIMIT = 128
_LOGGER = logging.getLogger(__name__)


class VoiceEvents(Protocol):
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None: ...

    async def on_guild_remove(self, guild: discord.Guild) -> None: ...


class _DiscordClient(discord.Client):
    def __init__(self, owner: DiscordGateway, *, intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self._owner = owner
        self.commands: DiscordCommands | None = None
        self.voice_events: VoiceEvents | None = None

    async def setup_hook(self) -> None:
        if self.commands is not None:
            await self.commands.register()

    async def on_ready(self) -> None:
        self._owner._discord_ready()  # pyright: ignore[reportPrivateUsage]

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if self.voice_events is not None:
            await self.voice_events.on_voice_state_update(member, before, after)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self.voice_events is not None:
            await self.voice_events.on_guild_remove(guild)


class DiscordGateway:
    """Runtime-owned gateway; voice connection/output belongs to the Session."""

    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        self.client = _DiscordClient(self, intents=intents)
        self._ready = asyncio.Event()
        self._startup_error: VoiceError | None = None
        self._started = False
        self._closing = False
        self._activity: discord.BaseActivity = discord.CustomActivity(
            name="Bereit für Musik"
        )
        self._sent_activity: discord.BaseActivity | None = None
        self._presence_task: asyncio.Task[None] | None = None
        self._bio_task: asyncio.Task[None] | None = None

    def bind_voice(self, voice: VoiceEvents) -> None:
        self.client.voice_events = voice

    def install_commands(
        self, access_path: Path, connect: Callable[[int], Awaitable[object]]
    ) -> None:
        self.client.commands = DiscordCommands(self.client, access_path, connect)

    async def start(self, token: str, *, prepare: Callable[[], None]) -> None:
        """Run until closed, reporting dependency/startup errors to readiness waiters."""
        if self._started:
            raise VoiceError("Discord client has already been started.")
        self._started = True
        try:
            prepare()
            self._presence_task = asyncio.create_task(
                self._update_presence(), name="discord-presence"
            )
            self._bio_task = asyncio.create_task(
                self._update_bio(), name="discord-daily-bio"
            )
            await self.client.start(token)
        except asyncio.CancelledError:
            await self.close()
            raise
        except discord.LoginFailure as error:
            self._startup_error = VoiceError("Discord authentication failed.")
            self._ready.set()
            await self.close()
            raise self._startup_error from error
        except VoiceError as error:
            self._startup_error = error
            self._ready.set()
            await self.close()
            raise
        except Exception as error:
            self._startup_error = VoiceError("Discord client failed to start.")
            self._ready.set()
            await self.close()
            raise self._startup_error from error
        finally:
            self._ready.set()
            await self._stop_profile_tasks()

    async def wait_until_ready(self) -> None:
        await self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def _discord_ready(self) -> None:
        self._sent_activity = None
        self._ready.set()

    def set_activity(
        self, track: ResolvedTrack | None = None, *, paused: bool = False
    ) -> None:
        if track is None:
            self._activity = discord.CustomActivity(name="Bereit für Musik")
            return
        title = " ".join((track.title or "YouTube").split()) or "YouTube"
        uploader = " ".join((track.uploader or "").split())
        if paused:
            self._activity = discord.CustomActivity(
                name=f"Pausiert: {title}"[:_PRESENCE_TEXT_LIMIT]
            )
        else:
            self._activity = discord.Activity(
                type=discord.ActivityType.listening,
                name=title[:_PRESENCE_TEXT_LIMIT],
                state=uploader[:_PRESENCE_TEXT_LIMIT] or None,
            )

    async def _update_presence(self) -> None:
        await self._ready.wait()
        while True:
            activity = self._activity
            if self.client.is_ready() and (
                self._sent_activity is None
                or activity.to_dict() != self._sent_activity.to_dict()
            ):
                try:
                    async with asyncio.timeout(10):
                        await self.client.change_presence(activity=activity)
                except Exception as error:
                    _LOGGER.warning(
                        "Discord presence update failed (%s); will retry.",
                        type(error).__name__,
                    )
                else:
                    self._sent_activity = activity
            await asyncio.sleep(_PRESENCE_INTERVAL_SECONDS)

    async def _update_bio(self) -> None:
        await self._ready.wait()
        await update_daily_bio(self.client)

    async def _stop_profile_tasks(self) -> None:
        tasks = tuple(
            task for task in (self._presence_task, self._bio_task) if task is not None
        )
        self._presence_task = self._bio_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        await self._stop_profile_tasks()
        await self.client.close()

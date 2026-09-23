# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Production composition. External work begins only on application startup."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from typing import cast

from fastapi import FastAPI

from ..application.auth import Auth
from ..application.access import Access
from ..config import AuthSettings, Settings, environment_values
from ..integrations.daily_bio import update_daily_bio
from ..integrations.discord_oauth import DiscordOAuth
from .api import create_app
from .commands import DiscordCommands
from .discord import DiscordOutput
from .gateway import DiscordGateway
from .runtime import Services, open_engine
from .schema import initialize
from .youtube import YouTubeMusicProvider, YouTubeProvider

_LOGGER = logging.getLogger(__name__)
_READY_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def open_runtime(
    settings: Settings, auth_settings: AuthSettings
) -> AsyncGenerator[Services]:
    if settings.database_url != auth_settings.database_url:
        raise ValueError("Engine and authentication must use the same database.")
    DiscordOutput.validate_dependencies()
    session_id = await initialize(settings.database_url)
    avatars = tuple(
        cast(
            list[str],
            json.loads(
                files("nahormaar_backend")
                .joinpath("avatars.json")
                .read_text(encoding="utf-8")
            ),
        )
    )
    gateway = DiscordGateway(settings.ffmpeg_path)
    access = Access.from_file(settings.database_url, auth_settings.access_path)
    closing = False

    async def run_client() -> None:
        await gateway.start(settings.token)
        if not closing:
            raise RuntimeError("Discord gateway stopped unexpectedly.")

    async with asyncio.TaskGroup() as tasks, gateway:
        client = tasks.create_task(run_client(), name="engine-discord-client")
        try:
            async with asyncio.timeout(_READY_TIMEOUT_SECONDS):
                await gateway.wait_until_ready()
            async with open_engine(
                session_id,
                auth=Auth(
                    auth_settings,
                    avatars,
                    access=access,
                    provider=DiscordOAuth(auth_settings),
                ),
                providers=(
                    YouTubeMusicProvider(settings.node_path),
                    YouTubeProvider(settings.node_path),
                ),
                audio=gateway.output,
                voice=gateway.output,
                directory=gateway,
                clock=lambda: datetime.now(UTC),
            ) as services:
                profile = tasks.create_task(
                    gateway.update_presence(services.session, services.metadata),
                    name="engine-discord-presence",
                )
                bio = tasks.create_task(
                    update_daily_bio(gateway), name="engine-discord-bio"
                )
                try:
                    commands = DiscordCommands(
                        gateway, services.session, services.access
                    )
                    try:
                        await commands.register()
                    except Exception as error:
                        _LOGGER.warning(
                            "engine.discord.commands_failed: %s", type(error).__name__
                        )
                    _LOGGER.info("engine.runtime.ready session_id=%s", session_id)
                    yield services
                finally:
                    services.session.events.close()
                    profile.cancel()
                    bio.cancel()
                    await asyncio.gather(profile, bio, return_exceptions=True)
        finally:
            closing = True
            client.cancel()
            await asyncio.gather(client, return_exceptions=True)
            _LOGGER.info("engine.runtime.client_stopped")


def create_application(
    *, shutdown_event: asyncio.Event, environ: Mapping[str, str] | None = None
) -> FastAPI:
    values = environment_values(environ)
    settings = Settings.from_env(values)
    auth_settings = AuthSettings.from_env(values)
    return create_app(
        lambda: open_runtime(settings, auth_settings),
        public_origin=auth_settings.public_origin,
        shutdown_event=shutdown_event,
    )

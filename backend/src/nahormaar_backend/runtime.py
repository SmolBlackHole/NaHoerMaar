# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Start and close the Discord client and player in one asyncio runtime."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .audio import VoiceError
from .config import Settings
from .catalog import MediaCatalog
from .discord_voice import DiscordVoice
from .playback import PlaybackController
from .youtube import YouTubeResolver


@asynccontextmanager
async def open_runtime(settings: Settings) -> AsyncGenerator[PlaybackController, None]:
    voice = DiscordVoice(settings.ffmpeg_path)
    resolver = YouTubeResolver(settings.node_path)
    catalog = MediaCatalog(settings.node_path)
    try:
        controller = await PlaybackController.create(
            settings.database_path,
            resolver,
            voice,
            metadata_resolver=catalog,
            catalog=catalog,
        )
    except BaseException:
        await asyncio.shield(catalog.close())
        await asyncio.shield(voice.close())
        raise
    closing = False
    ready = False

    async def run_client() -> None:
        await voice.start(settings.token)
        if not closing:
            message = (
                "Discord client stopped unexpectedly."
                if ready
                else "Discord client stopped before startup completed."
            )
            raise VoiceError(message)

    async with asyncio.TaskGroup() as tasks:
        client = tasks.create_task(run_client(), name="discord-client")

        async def close() -> None:
            errors: list[Exception] = []
            try:
                await controller.close()
            except Exception as exc:
                errors.append(exc)
            try:
                await voice.close()
            except Exception as exc:
                errors.append(exc)
            _, pending = await asyncio.wait({client}, timeout=5)
            if pending:
                client.cancel()
                await asyncio.gather(client, return_exceptions=True)
                errors.append(VoiceError("Discord client did not close in time."))
            if errors:
                raise ExceptionGroup("Backend shutdown failed.", errors)

        try:
            try:
                async with asyncio.timeout(30):
                    await voice.wait_until_ready()
            except TimeoutError as exc:
                raise VoiceError("Discord did not become ready in time.") from exc
            ready = True
            yield controller
        finally:
            closing = True
            cleanup = asyncio.create_task(close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Compose the shared Session and own the gateway and discovery lifetimes."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from .application.audio import VoiceError
from .application.catalog import MediaCatalog
from .application.radio import RadioCatalog
from .application.session import Session, SessionManager
from .config import Settings
from .integrations.catalog import create_media_catalog
from .integrations.discord_gateway import DiscordGateway
from .integrations.discord_voice import DiscordVoice
from .integrations.youtube import YouTubeResolver
from .integrations.discovery import DiscoveryExtractor
from .integrations.youtube_radio import YouTubeMusicRadio
from .persistence.player_store import SQLiteStore

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    session: Session
    catalog: MediaCatalog | None = None
    radio_catalog: RadioCatalog | None = None


@asynccontextmanager
async def open_runtime(
    settings: Settings, *, access_path: Path | None = None
) -> AsyncGenerator[RuntimeServices, None]:
    _LOGGER.info("runtime.starting")
    resources: list[Callable[[], Awaitable[None]]] = []
    client: asyncio.Task[None] | None = None
    closing = False
    cleanup_started = False
    ready = False

    async def close() -> None:
        errors: list[Exception] = []
        for release in reversed(resources):
            try:
                await release()
            except Exception as exc:
                errors.append(exc)
        if client is not None:
            _, pending = await asyncio.wait({client}, timeout=5)
            if pending:
                client.cancel()
                await asyncio.gather(client, return_exceptions=True)
                errors.append(VoiceError("Discord client did not close in time."))
        if errors:
            raise ExceptionGroup("Backend shutdown failed.", errors)

    async def cleanup() -> None:
        nonlocal closing, cleanup_started
        cleanup_started = True
        closing = True
        _LOGGER.info("runtime.stopping")
        task = asyncio.create_task(close(), name="runtime-cleanup")
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise
        finally:
            _LOGGER.info("runtime.cleanup_finished")

    try:
        gateway = DiscordGateway()
        resources.append(gateway.close)
        voice = DiscordVoice(
            settings.ffmpeg_path,
            client=gateway.client,
            activity=gateway.set_activity,
        )
        voice_cleanup = voice.close
        resources.append(voice_cleanup)
        gateway.bind_voice(voice)
        resolver = YouTubeResolver(settings.node_path)
        catalog = await create_media_catalog(settings.node_path)
        resources.append(catalog.close)
        radio_extractor = DiscoveryExtractor(settings.node_path)
        resources.append(radio_extractor.close)
        radio = RadioCatalog(YouTubeMusicRadio(radio_extractor.execute))
        resources.append(radio.close)
        manager = SessionManager()
        resources.append(manager.close)

        def store_factory() -> SQLiteStore:
            settings.database_path.parent.mkdir(parents=True, exist_ok=True)
            return SQLiteStore(settings.database_path)

        # Session creation takes ownership even when opening its worker fails.
        resources.remove(voice_cleanup)
        session = await manager.create(
            store_factory,
            resolver,
            voice,
            metadata_resolver=catalog,
            catalog=catalog,
            radio_catalog=radio,
        )
        if access_path is not None:
            gateway.install_commands(access_path, session.connect)

        async def run_client() -> None:
            await gateway.start(settings.token, prepare=voice.validate_dependencies)
            if not closing:
                message = (
                    "Discord client stopped unexpectedly."
                    if ready
                    else "Discord client stopped before startup completed."
                )
                raise VoiceError(message)

        async with asyncio.TaskGroup() as tasks:
            client = tasks.create_task(run_client(), name="discord-client")
            try:
                try:
                    async with asyncio.timeout(30):
                        await gateway.wait_until_ready()
                except TimeoutError as exc:
                    raise VoiceError("Discord did not become ready in time.") from exc
                ready = True
                await session.restore()
                _LOGGER.info("runtime.ready")
                yield RuntimeServices(session, catalog, radio)
            finally:
                await cleanup()
    finally:
        if not cleanup_started:
            await cleanup()

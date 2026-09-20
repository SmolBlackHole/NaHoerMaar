# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Build the HTTP application and own its lifespan."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from importlib.resources import files
from typing import cast
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..application.auth import Auth, IdentityProvider
from ..application.playback import PlaybackController
from ..config import AuthSettings, Settings
from ..integrations.discord_oauth import DiscordOAuth
from ..runtime import open_runtime
from .boundary import AuthBoundary
from .dependencies import ApiServices
from .errors import install_error_handlers
from .events import events_router
from .routes.auth import auth_router
from .routes.discovery import discovery_router
from .routes.player import player_router
from .routes.queue import queue_router
from .routes.radio import radio_router

type RuntimeFactory = Callable[[], AbstractAsyncContextManager[PlaybackController]]


def create_app(
    runtime_factory: RuntimeFactory | None = None,
    *,
    shutdown_event: asyncio.Event | None = None,
    auth_settings: AuthSettings | None = None,
    identity_provider: IdentityProvider | None = None,
) -> FastAPI:
    services = ApiServices()
    settings = auth_settings or AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        runtime = (
            runtime_factory()
            if runtime_factory is not None
            else open_runtime(Settings.from_env())
        )
        async with runtime as active:
            services.controller = active
            avatars = cast(
                list[str],
                json.loads(
                    files("nahormaar_backend")
                    .joinpath("avatars.json")
                    .read_text(encoding="utf-8")
                ),
            )
            services.authentication = Auth(
                settings,
                tuple(avatars),
                provider=identity_provider or DiscordOAuth(settings),
            )

            async def stop_streams() -> None:
                if shutdown_event is not None:
                    await shutdown_event.wait()
                    active.close_events()

            watcher = asyncio.create_task(stop_streams())
            try:
                yield
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                active.close_events()
                services.controller = None
                await services.auth().close()
                services.authentication = None

    app = FastAPI(title="NaHörMaar", version="0.1.0", lifespan=lifespan)
    app.add_middleware(AuthBoundary, service=services.auth)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1",
            "localhost",
            "[::1]",
            urlsplit(settings.public_origin).hostname or "localhost",
        ],
    )
    app.include_router(auth_router(services.auth))

    app.include_router(discovery_router(services))
    app.include_router(events_router(services))
    app.include_router(queue_router(services))
    app.include_router(player_router(services))
    app.include_router(radio_router(services))
    install_error_handlers(app)
    return app

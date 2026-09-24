# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Run one bot and its local HTTP API."""

import asyncio
import os
import socket

import uvicorn

from .engine.bootstrap import create_application
from .engine.logs import logging_configuration


class LocalServer(uvicorn.Server):
    """End SSE responses before Uvicorn waits for active requests to drain."""

    def __init__(self, config: uvicorn.Config, shutdown_event: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown_event = shutdown_event

    async def on_tick(self, counter: int) -> bool:
        return self._shutdown_event.is_set() or await super().on_tick(counter)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._shutdown_event.set()
        await super().shutdown(sockets)


def main() -> None:
    shutdown_event = asyncio.Event()
    config = uvicorn.Config(
        create_application(shutdown_event=shutdown_event),
        host=os.environ.get("NAHORMAAR_BIND_HOST", "127.0.0.1"),
        port=8000,
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
        log_config=logging_configuration(),
    )
    # Reserve the API port before the lifespan can log a second bot into Discord.
    with config.bind_socket() as listener:
        LocalServer(config, shutdown_event).run(sockets=[listener])


if __name__ == "__main__":
    main()

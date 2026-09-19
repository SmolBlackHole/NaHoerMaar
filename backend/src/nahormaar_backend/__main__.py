# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Run one bot and its local HTTP API."""

import asyncio
import socket

import uvicorn

from .api import create_app


class LocalServer(uvicorn.Server):
    """End SSE responses before Uvicorn waits for active requests to drain."""

    def __init__(self, config: uvicorn.Config, shutdown_event: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown_event = shutdown_event

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._shutdown_event.set()
        await super().shutdown(sockets)


def main() -> None:
    shutdown_event = asyncio.Event()
    config = uvicorn.Config(
        create_app(shutdown_event=shutdown_event),
        host="127.0.0.1",
        port=8000,
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
    )
    LocalServer(config, shutdown_event).run()


if __name__ == "__main__":
    main()

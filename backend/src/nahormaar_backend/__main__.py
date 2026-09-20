# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Run one bot and its local HTTP API."""

import asyncio
import copy
import logging
import socket

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from .api import create_app


class AccessLogFilter(logging.Filter):
    """OAuth callback query strings contain one-use credentials."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, protocol, status = record.args
            if isinstance(path, str) and path.startswith("/api/auth/"):
                record.args = (client, method, path.split("?", 1)[0], protocol, status)
        return True


class LocalServer(uvicorn.Server):
    """End SSE responses before Uvicorn waits for active requests to drain."""

    def __init__(self, config: uvicorn.Config, shutdown_event: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown_event = shutdown_event

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._shutdown_event.set()
        await super().shutdown(sockets)


def main() -> None:
    logging.getLogger("uvicorn.access").addFilter(AccessLogFilter())
    log_config = copy.deepcopy(LOGGING_CONFIG)
    for formatter in log_config["formatters"].values():
        formatter["fmt"] = "%(asctime)s pid=%(process)d " + formatter["fmt"]
        formatter["datefmt"] = "%Y-%m-%dT%H:%M:%S%z"
    log_config["loggers"]["nahormaar_backend"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    shutdown_event = asyncio.Event()
    config = uvicorn.Config(
        create_app(shutdown_event=shutdown_event),
        host="127.0.0.1",
        port=8000,
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
        log_config=log_config,
    )
    LocalServer(config, shutdown_event).run()


if __name__ == "__main__":
    main()

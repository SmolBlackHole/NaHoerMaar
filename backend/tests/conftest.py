# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Coroutine, Generator
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
import socket
import sys
from typing import Any, TypeVar, cast

import discord
import pytest

from engine.database import database_url, drop_test_schemas


T = TypeVar("T")


@pytest.fixture(autouse=True)
def isolate_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Tests must never inherit the running bot's credentials, files or endpoints."""
    nodeid = str(cast(Any, request).node.nodeid)
    if sys.platform == "win32" and "test_processes.py" not in nodeid:

        def selector_run(
            main: Coroutine[Any, Any, T], *, debug: bool | None = None
        ) -> T:
            with asyncio.Runner(
                debug=debug, loop_factory=asyncio.SelectorEventLoop
            ) as runner:
                return runner.run(main)

        monkeypatch.setattr(asyncio, "run", selector_run)

    monkeypatch.chdir(tmp_path)
    schema_prefix = "t_" + sha256(str(tmp_path).encode()).hexdigest()[:12]
    monkeypatch.setenv("NAHORMAAR_POSTGRES_SCHEMA_PREFIX", schema_prefix)
    runtime_database_url = database_url(tmp_path / "runtime")
    for name, value in {
        "DISCORD_TOKEN": "test-token",
        "DISCORD_CLIENT_ID": "123",
        "DISCORD_CLIENT_SECRET": "test-secret",
        "DATABASE_URL": runtime_database_url,
        "ACCESS_PATH": str(tmp_path / "access.toml"),
    }.items():
        monkeypatch.setenv(name, value)

    async def no_login(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Real Discord login is disabled in tests.")

    monkeypatch.setattr(discord.Client, "login", no_login)
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check_address(address: object) -> None:
        parts = cast(tuple[object, ...], address) if isinstance(address, tuple) else ()
        if len(parts) >= 2:
            host, port = parts[:2]
            try:
                loopback = ip_address(str(host)).is_loopback
            except ValueError:
                loopback = False
            if loopback and port not in (8000, 3000):
                return
        raise RuntimeError("Network access outside a test server is disabled.")

    def test_connect(sock: socket.socket, address: object) -> None:
        check_address(address)
        connect(sock, address)  # type: ignore[arg-type]

    def test_connect_ex(sock: socket.socket, address: object) -> int:
        check_address(address)
        return connect_ex(sock, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", test_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", test_connect_ex)
    yield
    drop_test_schemas(schema_prefix)

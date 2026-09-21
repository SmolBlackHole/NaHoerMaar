# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from ipaddress import ip_address
from pathlib import Path
import socket
from typing import cast

import discord
import pytest


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests must never inherit the running bot's credentials, files or endpoints."""
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "DISCORD_TOKEN": "test-token",
        "DISCORD_CLIENT_ID": "123",
        "DISCORD_CLIENT_SECRET": "test-secret",
        "DATABASE_PATH": str(tmp_path / "isolated.sqlite3"),
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
            # Local integration servers bind ephemeral ports. The dev servers
            # and external services are never test targets.
            if loopback and port not in (8000, 3000, 3012):
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

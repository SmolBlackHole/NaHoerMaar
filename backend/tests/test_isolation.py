# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import socket
from pathlib import Path

import discord
import pytest

from nahormaar_backend.config import AuthSettings


def test_default_settings_cannot_open_live_database(tmp_path: Path) -> None:
    settings = AuthSettings.from_env()
    assert Path.cwd() == tmp_path
    assert settings.database_path == tmp_path / "isolated.sqlite3"
    assert settings.access_path == tmp_path / "access.toml"
    assert settings.client_secret == "test-secret"  # noqa: S105


@pytest.mark.parametrize(
    "address", [("127.0.0.1", 8000), ("::1", 3012), ("192.0.2.1", 443)]
)
def test_live_and_external_connections_are_rejected(address: tuple[str, int]) -> None:
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="outside a test server"):
            connection.connect(address)
        with pytest.raises(RuntimeError, match="outside a test server"):
            connection.connect_ex(address)


def test_real_discord_login_is_rejected() -> None:
    async def scenario() -> None:
        async with discord.Client(intents=discord.Intents.none()) as client:
            with pytest.raises(RuntimeError, match="Real Discord login"):
                await client.login("test-token")

    asyncio.run(scenario())

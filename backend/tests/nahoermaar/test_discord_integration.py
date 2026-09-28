# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from nahoermaar.config import AuthSettings, DiscordSettings
from nahoermaar.integrations import main as integrations_main
from nahoermaar.integrations.discord import SummonHandler, load_quotes, quote_for_day
from nahoermaar.integrations.discord_oauth import DiscordOAuth
from nahoermaar.integrations.main import (
    IntegrationsPreparation,
    complete_integrations_module,
    prepare_integrations_module,
)
from nahoermaar.player.playback import PlaybackTransport


def _prepare(tmp_path: Path, *, token: str = "") -> IntegrationsPreparation:
    return prepare_integrations_module(
        AuthSettings(
            "http://localhost:3000",
            "123",
            "secret",
            tmp_path / "access.toml",
        ),
        DiscordSettings(token, Path("ffmpeg"), tmp_path / "quotes.toml"),
        node_path=Path("node"),
        avatar_directory=tmp_path / "avatars",
    )


async def _summon(_discord_id: str, _channel_id: int, _correlation_id: UUID) -> None:
    pass


def test_disabled_integrations_keep_external_adapters_without_runtime(
    tmp_path: Path,
) -> None:
    preparation = _prepare(tmp_path)

    module = complete_integrations_module(preparation, _summon)

    assert isinstance(module.identity, DiscordOAuth)
    assert tuple(provider.key for provider in module.catalog_providers) == (
        "youtube",
        "youtube_music",
    )
    assert module.avatars is preparation.avatars
    assert module.housekeeping is preparation.housekeeping
    assert module.gateway is None
    assert module.playback_transport is None
    assert module.gateway_lifecycle is None


def test_enabled_integrations_complete_gateway_and_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    transport = cast(PlaybackTransport, object())

    class Gateway:
        output = transport

        def __init__(
            self,
            token: str,
            ffmpeg_path: Path,
            quotes_path: Path,
            summon: Callable[[str, int, UUID], Awaitable[None]],
        ) -> None:
            calls.append((token, ffmpeg_path, quotes_path, summon))

        async def open(self) -> None:
            calls.append("open")

        async def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(integrations_main, "DiscordGateway", Gateway)
    preparation = _prepare(tmp_path, token="token")  # noqa: S106

    module = complete_integrations_module(
        preparation,
        cast(SummonHandler, _summon),
    )

    assert module.gateway is not None
    assert module.playback_transport is transport
    assert module.gateway_lifecycle is not None
    assert module.gateway_lifecycle.name == "discord"
    assert module.gateway_lifecycle.start is not None
    assert module.gateway_lifecycle.close is not None

    async def lifecycle() -> None:
        assert module.gateway_lifecycle is not None
        assert module.gateway_lifecycle.start is not None
        assert module.gateway_lifecycle.close is not None
        await module.gateway_lifecycle.start()
        await module.gateway_lifecycle.close()

    asyncio.run(lifecycle())
    assert calls == [
        ("token", Path("ffmpeg"), tmp_path / "quotes.toml", _summon),
        "open",
        "close",
    ]


def test_daily_quote_is_stable_for_one_day(tmp_path: Path) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text(
        '[quotes]\nde = ["Eins", "Zwei"]\nnl = ["Drie"]\n',
        encoding="utf-8",
    )
    quotes = load_quotes(path)

    assert quotes == ("Eins", "Zwei", "Drie")
    assert quote_for_day(quotes, date(2026, 9, 25)) == quote_for_day(
        quotes,
        date(2026, 9, 25),
    )


def test_quotes_reject_empty_language_groups(tmp_path: Path) -> None:
    path = tmp_path / "quotes.toml"
    path.write_text("[quotes]\nde = []\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"quotes\.de"):
        load_quotes(path)

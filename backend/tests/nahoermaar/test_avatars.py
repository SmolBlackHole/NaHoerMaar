# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import httpx
import pytest

from nahoermaar.integrations.avatars import (
    AvatarUnavailableError,
    DiscordAvatarStore,
)


def test_discord_avatar_is_downloaded_once_and_served_from_cache(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"avatar-bytes",
            request=request,
        )

    store = DiscordAvatarStore(tmp_path, httpx.MockTransport(respond))
    local_url = store.public_url(
        "1377708476259897478",
        source_url=("https://cdn.discordapp.com/avatars/1377708476259897478/hash.png"),
    )
    version = local_url.rsplit("=", 1)[1]

    async def scenario() -> None:
        first = await store.get("1377708476259897478", version)
        second = await store.get("1377708476259897478", version)
        assert first == second
        assert first.media_type == "image/png"
        assert first.path.read_bytes() == b"avatar-bytes"

    asyncio.run(scenario())
    assert len(requests) == 1


def test_invalid_avatar_response_does_not_create_a_cache_entry(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"not an image",
            request=request,
        )

    store = DiscordAvatarStore(tmp_path, httpx.MockTransport(respond))
    local_url = store.public_url("1377708476259897478", avatar_hash="hash")
    version = local_url.rsplit("=", 1)[1]

    async def scenario() -> None:
        with pytest.raises(AvatarUnavailableError, match="invalid avatar image"):
            await store.get("1377708476259897478", version)

    asyncio.run(scenario())
    assert list(tmp_path.iterdir()) == []


def test_distinct_discord_avatar_versions_remain_cached(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=str(request.url).encode(),
            request=request,
        )

    store = DiscordAvatarStore(tmp_path, httpx.MockTransport(respond))
    discord_id = "1377708476259897478"
    first_url = store.public_url(
        discord_id,
        source_url=f"https://cdn.discordapp.com/avatars/{discord_id}/first.png",
    )
    second_url = store.public_url(
        discord_id,
        source_url=f"https://cdn.discordapp.com/avatars/{discord_id}/second.png",
    )
    first_version = first_url.rsplit("=", 1)[1]
    second_version = second_url.rsplit("=", 1)[1]

    async def scenario() -> None:
        first = await store.get(discord_id, first_version)
        second = await store.get(discord_id, second_version)
        assert first.path.is_file()
        assert second.path.is_file()
        assert await store.get(discord_id, first_version) == first
        assert await store.get(discord_id, second_version) == second

    asyncio.run(scenario())
    assert len(requests) == 2


def test_avatar_store_rejects_non_discord_sources(tmp_path: Path) -> None:
    store = DiscordAvatarStore(tmp_path)
    with pytest.raises(AvatarUnavailableError, match="Discord CDN"):
        store.public_url(
            "1377708476259897478",
            source_url="https://example.com/avatar.png",
        )


def test_avatar_prune_keeps_current_and_newest_versions(tmp_path: Path) -> None:
    discord_id = "1377708476259897478"
    store = DiscordAvatarStore(tmp_path)
    active_url = store.public_url(
        discord_id,
        source_url=f"https://cdn.discordapp.com/avatars/{discord_id}/active.png",
    )
    active_version = active_url.rsplit("=", 1)[1]
    active = tmp_path / f"{discord_id}-{active_version}.png"
    superseded = tmp_path / f"{discord_id}-1111111111111111.png"
    newest = tmp_path / f"{discord_id}-2222222222222222.png"
    temporary = tmp_path / f".{discord_id}-stale.tmp"
    for path in (active, superseded, newest, temporary):
        path.write_bytes(b"avatar")

    cutoff = datetime(2026, 9, 27, 12, tzinfo=UTC)
    old = (cutoff - timedelta(days=2)).timestamp()
    recent = (cutoff + timedelta(minutes=1)).timestamp()
    for path in (active, superseded, temporary):
        os.utime(path, (old, old))
    os.utime(newest, (recent, recent))

    removed = asyncio.run(store.prune(cutoff))

    assert removed == 2
    assert active.is_file()
    assert newest.is_file()
    assert not superseded.exists()
    assert not temporary.exists()

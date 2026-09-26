# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Local cache for Discord profile images."""

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

_DISCORD_ID = re.compile(r"[1-9][0-9]{0,19}")
_VERSION = re.compile(r"[0-9a-f]{16}")
_ALLOWED_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
_MEDIA_TYPES = {
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class AvatarUnavailableError(RuntimeError):
    """Raised when a Discord avatar cannot be served safely."""


@dataclass(frozen=True, slots=True)
class AvatarAsset:
    path: Path
    media_type: str


class DiscordAvatarStore:
    """Remember Discord avatar sources and cache their bytes locally."""

    __slots__ = ("_directory", "_sources", "_transport")

    def __init__(
        self,
        directory: Path,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._directory = directory
        self._sources: dict[tuple[str, str], str] = {}
        self._transport = transport

    def public_url(
        self,
        discord_id: str,
        *,
        avatar_hash: str | None = None,
        source_url: str | None = None,
    ) -> str:
        """Register a trusted Discord source and return its local API URL."""
        _validate_discord_id(discord_id)
        source = _source_url(discord_id, avatar_hash, source_url)
        version = hashlib.sha256(source.encode()).hexdigest()[:16]
        self._sources[(discord_id, version)] = source
        return f"/api/avatars/discord/{discord_id}?v={version}"

    async def get(self, discord_id: str, version: str) -> AvatarAsset:
        """Return a cached image, downloading it once when needed."""
        _validate_discord_id(discord_id)
        if _VERSION.fullmatch(version) is None:
            raise AvatarUnavailableError("Invalid avatar version.")
        cached = self._cached(discord_id, version)
        if cached is not None:
            return cached

        source = self._sources.get((discord_id, version))
        if source is None:
            default_source = _default_avatar_url(discord_id)
            default_version = hashlib.sha256(default_source.encode()).hexdigest()[:16]
            if version == default_version:
                source = default_source
        if source is None:
            raise AvatarUnavailableError("Avatar source is not available.")

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(10.0),
                transport=self._transport,
            ) as client:
                response = await client.get(source)
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise AvatarUnavailableError("Discord avatar download failed.") from error

        final_host = (urlsplit(str(response.url)).hostname or "").lower()
        if final_host not in _ALLOWED_HOSTS:
            raise AvatarUnavailableError(
                "Discord avatar redirected to an invalid host."
            )
        media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        extension = _MEDIA_TYPES.get(media_type)
        content = response.content
        if extension is None or not content or len(content) > _MAX_IMAGE_BYTES:
            raise AvatarUnavailableError("Discord returned an invalid avatar image.")

        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / f"{discord_id}-{version}.{extension}"
        temporary = self._directory / f".{discord_id}-{uuid4().hex}.tmp"
        try:
            await asyncio.to_thread(temporary.write_bytes, content)
            await asyncio.to_thread(os.replace, temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return AvatarAsset(target, media_type)

    def _cached(self, discord_id: str, version: str) -> AvatarAsset | None:
        for media_type, extension in _MEDIA_TYPES.items():
            candidate = self._directory / f"{discord_id}-{version}.{extension}"
            if candidate.is_file():
                return AvatarAsset(candidate, media_type)
        return None


def _source_url(
    discord_id: str,
    avatar_hash: str | None,
    source_url: str | None,
) -> str:
    if source_url is not None:
        parsed = urlsplit(source_url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS
        ):
            raise AvatarUnavailableError("Avatar source is not a Discord CDN URL.")
        return source_url
    if avatar_hash:
        extension = "gif" if avatar_hash.startswith("a_") else "webp"
        return (
            f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}."
            f"{extension}?size=256"
        )
    return _default_avatar_url(discord_id)


def _default_avatar_url(discord_id: str) -> str:
    index = (int(discord_id) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def _validate_discord_id(discord_id: str) -> None:
    if _DISCORD_ID.fullmatch(discord_id) is None or int(discord_id) >= 2**64:
        raise AvatarUnavailableError("Invalid Discord user ID.")

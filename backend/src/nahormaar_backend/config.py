# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Local runtime configuration and executable checks."""

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import imageio_ffmpeg  # type: ignore[import-untyped]
from dotenv import dotenv_values
from sqlalchemy import make_url

from .domain.identity import discord_id


class ConfigurationError(ValueError):
    pass


def database_url(values: Mapping[str, str]) -> str:
    raw = values.get("DATABASE_URL", "").strip()
    if not raw:
        raise ConfigurationError("Set DATABASE_URL in the local environment.")
    try:
        url = make_url(raw)
    except ValueError as exc:
        raise ConfigurationError("DATABASE_URL is not a valid SQLAlchemy URL.") from exc
    if url.drivername != "postgresql+psycopg":
        raise ConfigurationError("DATABASE_URL must use PostgreSQL with psycopg.")
    return raw


def environment_values(environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
    if environ is not None:
        return environ
    return {
        key: value for key, value in dotenv_values(".env").items() if value is not None
    } | dict(os.environ)


CALLBACK_PATH = "/api/auth/discord/callback"


@dataclass(frozen=True, slots=True)
class AuthSettings:
    public_origin: str
    client_id: str
    client_secret: str = field(repr=False)
    database_url: str = field(repr=False)
    access_path: Path

    def __post_init__(self) -> None:
        url = urlsplit(self.public_origin)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or self.public_origin != f"{url.scheme}://{url.netloc}"
            or (
                url.scheme == "http"
                and url.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ConfigurationError(
                "PUBLIC_ORIGIN must be an HTTPS origin (HTTP is allowed on loopback only)."
            )
        if self.client_id and not discord_id(self.client_id):
            raise ConfigurationError(
                "DISCORD_CLIENT_ID must be a Discord application ID."
            )

    @property
    def secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return self.public_origin + CALLBACK_PATH

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthSettings":
        values = environment_values(environ)
        origin = (
            values.get("PUBLIC_ORIGIN", "http://localhost:3000").strip().rstrip("/")
        )
        return cls(
            origin,
            values.get("DISCORD_CLIENT_ID", "").strip(),
            values.get("DISCORD_CLIENT_SECRET", "").strip(),
            database_url(values),
            Path(values.get("ACCESS_PATH") or "config/access.toml").resolve(),
        )


def ffmpeg_executable(override: str | None = None) -> Path:
    """Prefer an explicit override; otherwise use the packaged local binary."""
    try:
        executable: str = override or imageio_ffmpeg.get_ffmpeg_exe()
    except RuntimeError as exc:
        raise ConfigurationError("No FFmpeg binary is available.") from exc
    resolved = shutil.which(executable)
    if resolved is None:
        raise ConfigurationError("FFMPEG_PATH does not point to an executable.")
    return Path(resolved).resolve()


def executable_version(executable: Path, option: str = "--version") -> str:
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(  # noqa: S603 - a resolved executable, no shell
            (str(executable), option),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        return result.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise ConfigurationError(f"Cannot run {executable.name}.") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    token: str = field(repr=False)
    database_url: str = field(repr=False)
    ffmpeg_path: Path
    node_path: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        values = environment_values(environ)
        token = values.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("Set DISCORD_TOKEN in the local environment.")
        node = shutil.which(values.get("NODE_PATH") or "node")
        if node is None:
            raise ConfigurationError(
                "Node.js 22+ is required; set NODE_PATH if needed."
            )
        node_path = Path(node).resolve()
        version = executable_version(node_path)
        try:
            major = int(version.removeprefix("v").split(".")[0])
        except ValueError as exc:
            raise ConfigurationError("Cannot read the Node.js version.") from exc
        if major < 22:
            raise ConfigurationError("Node.js 22+ is required.")
        ffmpeg = ffmpeg_executable(values.get("FFMPEG_PATH"))
        executable_version(ffmpeg, "-version")
        database = database_url(values)
        return cls(token, database, ffmpeg, node_path)


if __name__ == "__main__":
    binary = ffmpeg_executable(os.environ.get("FFMPEG_PATH"))
    print(f"FFmpeg: {binary}")
    print(executable_version(binary, "-version"))

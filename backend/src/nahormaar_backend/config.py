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

import imageio_ffmpeg  # type: ignore[import-untyped]
from dotenv import dotenv_values


class ConfigurationError(ValueError):
    pass


def environment_values(environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
    if environ is not None:
        return environ
    return {
        key: value for key, value in dotenv_values(".env").items() if value is not None
    } | dict(os.environ)


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
    database_path: Path
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
        database = Path(values.get("DATABASE_PATH") or "data/player.sqlite3").resolve()
        return cls(token, database, ffmpeg, node_path)


if __name__ == "__main__":
    binary = ffmpeg_executable(os.environ.get("FFMPEG_PATH"))
    print(f"FFmpeg: {binary}")
    print(executable_version(binary, "-version"))

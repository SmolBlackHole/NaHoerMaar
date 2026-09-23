# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import shutil
from pathlib import Path

import pytest

import nahormaar_backend.config as config_module
from nahormaar_backend.config import (
    ConfigurationError,
    Settings,
    executable_version,
    ffmpeg_executable,
)


def _stub_executables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    node = tmp_path / "node.exe"
    ffmpeg = tmp_path / "ffmpeg.exe"

    def fake_which(executable: str) -> str | None:
        if executable in {"node", "env-node", "file-node"}:
            return str(node)
        if executable in {"env-ffmpeg", "file-ffmpeg"}:
            return str(ffmpeg)
        return None

    def fake_version(executable: Path, option: str = "--version") -> str:
        del option
        return "v24.0.0" if executable.name == "node.exe" else "ffmpeg version 7.1"

    def fake_ffmpeg(override: str | None = None) -> Path:
        return Path(fake_which(override or "env-ffmpeg") or "missing")

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(config_module, "executable_version", fake_version)
    monkeypatch.setattr(config_module, "ffmpeg_executable", fake_ffmpeg)


def test_environment_overrides_dotenv_and_resolves_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_executables(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "DISCORD_TOKEN=file-token",
                "DATABASE_URL=postgresql+psycopg://file:secret@db/file",
                "NODE_PATH=file-node",
                "FFMPEG_PATH=file-ffmpeg",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env:secret@db/env")
    monkeypatch.setenv("NODE_PATH", "env-node")
    monkeypatch.setenv("FFMPEG_PATH", "env-ffmpeg")

    settings = Settings.from_env()

    assert settings.token == "env-token"  # noqa: S105 - synthetic test credential
    assert settings.database_url == "postgresql+psycopg://env:secret@db/env"
    assert settings.node_path == (tmp_path / "node.exe").resolve()
    assert settings.ffmpeg_path == tmp_path / "ffmpeg.exe"


def test_explicit_environment_is_isolated_and_token_is_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_executables(monkeypatch, tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "ambient-secret")
    settings = Settings.from_env(
        {
            "DISCORD_TOKEN": "explicit-secret",
            "DATABASE_URL": "postgresql+psycopg://explicit:secret@db/explicit",
            "NODE_PATH": "node",
        }
    )

    representation = repr(settings)
    assert settings.token == "explicit-secret"  # noqa: S105 - synthetic credential
    assert "explicit-secret" not in representation
    assert "ambient-secret" not in representation
    assert "token=" not in representation


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({}, "DISCORD_TOKEN"),
        ({"DISCORD_TOKEN": "   "}, "DISCORD_TOKEN"),
    ],
)
def test_missing_or_invalid_identity_settings_are_rejected(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env(environment)


def test_missing_and_old_node_overrides_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = {
        "DISCORD_TOKEN": "token",
        "DATABASE_URL": "postgresql+psycopg://test:secret@db/test",
    }

    def missing_executable(executable: str) -> str | None:
        del executable
        return None

    monkeypatch.setattr(shutil, "which", missing_executable)
    with pytest.raises(ConfigurationError, match=r"Node\.js 22"):
        Settings.from_env(base | {"NODE_PATH": "missing-node"})

    node = tmp_path / "node.exe"

    def existing_node(executable: str) -> str | None:
        del executable
        return str(node)

    def old_node_version(executable: Path, option: str = "--version") -> str:
        del executable, option
        return "v21.9.0"

    monkeypatch.setattr(shutil, "which", existing_node)
    monkeypatch.setattr(config_module, "executable_version", old_node_version)
    with pytest.raises(ConfigurationError, match=r"Node\.js 22"):
        Settings.from_env(base | {"NODE_PATH": str(node)})


def test_invalid_ffmpeg_override_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="FFMPEG_PATH"):
        ffmpeg_executable("definitely-not-a-real-nahormaar-ffmpeg")


def test_packaged_ffmpeg_binary_reports_its_version() -> None:
    executable = ffmpeg_executable()
    version = executable_version(executable, "-version")

    assert executable.is_file()
    assert version.lower().startswith("ffmpeg version")

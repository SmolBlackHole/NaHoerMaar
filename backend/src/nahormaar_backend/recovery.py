# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Consistent SQLite backups and explicit offline restoration."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from .config import environment_values
from .engine.schema import REVISION

DEFAULT_KEEP = 14
_BACKUP_NAME = re.compile(r"^engine-\d{8}T\d{12}Z\.sqlite3$")


class RecoveryError(RuntimeError):
    """A backup cannot be trusted or a restore cannot be completed safely."""


def configured_database_path(environ: Mapping[str, str] | None = None) -> Path:
    values = environment_values(environ)
    return Path(values.get("DATABASE_PATH") or "data/engine.sqlite3").resolve()


def _readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def verify_database(path: Path) -> None:
    """Reject damaged databases and files outside the current engine schema."""

    path = path.resolve()
    if not path.is_file():
        raise RecoveryError(f"Database does not exist: {path}")
    try:
        with closing(_readonly(path)) as database:
            integrity = tuple(
                str(row[0]) for row in database.execute("PRAGMA integrity_check")
            )
            if integrity != ("ok",):
                raise RecoveryError(
                    f"SQLite integrity check failed: {'; '.join(integrity)}"
                )
            if tuple(database.execute("PRAGMA foreign_key_check")):
                raise RecoveryError("SQLite foreign-key check failed.")
            revisions = tuple(
                row[0]
                for row in database.execute("SELECT version_num FROM alembic_version")
            )
            if revisions != (REVISION,):
                raise RecoveryError(
                    f"Expected engine schema {REVISION}; found {revisions or 'none'}."
                )
            sessions = database.execute(
                "SELECT COUNT(*) FROM listening_sessions"
            ).fetchone()
            if sessions is None or sessions[0] != 1:
                raise RecoveryError(
                    "A recovery database must contain one listening session."
                )
    except RecoveryError:
        raise
    except sqlite3.Error as exc:
        raise RecoveryError(f"Cannot verify SQLite database: {path}") from exc


def _copy_database(source: Path, destination: Path) -> None:
    try:
        with closing(_readonly(source)) as source_database:
            with closing(sqlite3.connect(destination, timeout=30)) as target_database:
                source_database.backup(target_database)
    except sqlite3.Error as exc:
        raise RecoveryError(f"Cannot copy SQLite database: {source}") from exc


def _temporary_path(directory: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".nahormaar-recovery-", suffix=".sqlite3", dir=directory
    )
    os.close(descriptor)
    return Path(name)


def _prune_backups(directory: Path, keep: int) -> None:
    managed = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and _BACKUP_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for stale in managed[keep:]:
        stale.unlink()


def backup_database(
    source: Path,
    directory: Path | None = None,
    *,
    keep: int = DEFAULT_KEEP,
    now: datetime | None = None,
) -> Path:
    """Create and verify an online backup, then apply bounded retention."""

    if type(keep) is not int or keep < 1:
        raise ValueError("Backup retention must be a positive integer.")
    source = source.resolve()
    if not source.is_file():
        raise RecoveryError(f"Database does not exist: {source}")
    directory = (directory or source.parent / "backups").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    filename = f"engine-{timestamp:%Y%m%dT%H%M%S%fZ}.sqlite3"
    destination = directory / filename
    if destination.exists():
        raise RecoveryError(f"Backup already exists: {destination}")
    temporary = _temporary_path(directory)
    try:
        _copy_database(source, temporary)
        verify_database(temporary)
        os.replace(temporary, destination)
        _prune_backups(directory, keep)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def restore_database(source: Path, destination: Path, *, replace: bool = False) -> Path:
    """Validate and atomically install a backup into an offline destination."""

    source, destination = source.resolve(), destination.resolve()
    verify_database(source)
    if destination.exists() and not replace:
        raise RecoveryError(
            f"Database already exists: {destination}. Pass --replace after stopping the backend."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination.parent)
    try:
        _copy_database(source, temporary)
        verify_database(temporary)
        os.replace(temporary, destination)
        for suffix in ("-wal", "-shm"):
            Path(f"{destination}{suffix}").unlink(missing_ok=True)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    database = configured_database_path(environ)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="Create a verified online backup.")
    backup.add_argument("--database", type=Path, default=database)
    backup.add_argument("--directory", type=Path)
    backup.add_argument("--keep", type=int, default=DEFAULT_KEEP)

    verify = commands.add_parser("verify", help="Verify a recovery database.")
    verify.add_argument("path", type=Path)

    restore = commands.add_parser("restore", help="Restore into an offline database.")
    restore.add_argument("path", type=Path)
    restore.add_argument("--database", type=Path, default=database)
    restore.add_argument("--replace", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    arguments = _parser(environ).parse_args(argv)
    try:
        if arguments.command == "backup":
            created = backup_database(
                arguments.database, arguments.directory, keep=arguments.keep
            )
            print(f"Backup created: {created}")
        elif arguments.command == "verify":
            verify_database(arguments.path)
            print(f"Backup verified: {arguments.path.resolve()}")
        else:
            restored = restore_database(
                arguments.path, arguments.database, replace=arguments.replace
            )
            print(f"Database restored: {restored}")
    except (RecoveryError, ValueError) as exc:
        print(f"Recovery failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

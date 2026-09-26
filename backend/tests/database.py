# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""PostgreSQL schema isolation shared by the new backend tests."""

from hashlib import sha256
import os
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import make_url

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://nahormaar:nahormaar-test-only@127.0.0.1:55432/nahormaar_test"
)


def _base_url() -> str:
    return os.environ.get("NAHORMAAR_POSTGRES_TEST_URL", DEFAULT_TEST_DATABASE_URL)


def _schema(path: Path) -> str:
    prefix = os.environ.get("NAHORMAAR_POSTGRES_SCHEMA_PREFIX", "test")
    digest = sha256(str(path.resolve()).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


def database_url(path: Path) -> str:
    """Create and address one PostgreSQL schema for a test-owned database key."""
    base = make_url(_base_url())
    schema = _schema(path)
    dsn = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )
    return base.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)


def drop_test_schemas(prefix: str) -> None:
    """Remove only schemas created for one pytest case."""
    base = make_url(_base_url())
    dsn = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as connection:
        schemas = connection.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name LIKE %s",
            (f"{prefix}\\_%",),
        ).fetchall()
        for (schema,) in schemas:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )

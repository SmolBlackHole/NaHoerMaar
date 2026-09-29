# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Generate or check the nahoermaar client contract without starting services."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
TARGET = ROOT / "frontend/app/core/api/schema.generated.ts"
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
OPERATION_ID = re.compile(r"^[a-z][A-Za-z0-9]*$")
TYPESCRIPT_SPDX_HEADER = """// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

"""


def validate_operation_ids(schema: dict[str, object]) -> None:
    """Reject implicit, duplicate or non-lowerCamel operation identifiers."""
    identifiers: list[str] = []
    raw_paths = schema.get("paths")
    if not isinstance(raw_paths, Mapping):
        raise SystemExit("OpenAPI schema has no path map.")
    paths = cast(Mapping[str, object], raw_paths)
    for path, path_item in paths.items():
        if not isinstance(path_item, Mapping):
            continue
        operations = cast(Mapping[str, object], path_item)
        for method, operation in operations.items():
            if method not in HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            operation_document = cast(Mapping[str, object], operation)
            operation_id = operation_document.get("operationId")
            if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(
                operation_id
            ):
                raise SystemExit(
                    f"{method.upper()} {path} needs an explicit lowerCamel operationId."
                )
            identifiers.append(operation_id)
            _validate_error_responses(method, path, operation_document)
    duplicates = sorted(
        identifier
        for identifier in set(identifiers)
        if identifiers.count(identifier) > 1
    )
    if duplicates:
        raise SystemExit(f"Duplicate OpenAPI operationIds: {', '.join(duplicates)}")


def _validate_error_responses(
    method: str,
    path: str,
    operation: Mapping[str, object],
) -> None:
    raw_responses = operation.get("responses")
    if not isinstance(raw_responses, Mapping):
        raise SystemExit(f"{method.upper()} {path} has no response documents.")
    responses = cast(Mapping[str, object], raw_responses)
    if "500" not in responses:
        raise SystemExit(f"{method.upper()} {path} must document ErrorView for 500.")
    for status, raw_response in responses.items():
        if not status.isdigit() or int(status) < 400:
            continue
        if not isinstance(raw_response, Mapping):
            raise SystemExit(
                f"{method.upper()} {path} has an invalid {status} response."
            )
        response = cast(Mapping[str, object], raw_response)
        raw_content = response.get("content")
        if not isinstance(raw_content, Mapping):
            raise SystemExit(
                f"{method.upper()} {path} must document {status} as ErrorView."
            )
        content = cast(Mapping[str, object], raw_content)
        raw_json = content.get("application/json")
        if not isinstance(raw_json, Mapping):
            raise SystemExit(
                f"{method.upper()} {path} must document {status} as ErrorView."
            )
        json_document = cast(Mapping[str, object], raw_json)
        if json_document.get("schema") != {"$ref": "#/components/schemas/ErrorView"}:
            raise SystemExit(
                f"{method.upper()} {path} must document {status} as ErrorView."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--schema", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.schema:
        import asyncio

        sys.path.insert(0, str(BACKEND_SRC))
        from nahoermaar.api.app import create_app
        from nahoermaar.bootstrap import bootstrap
        from nahoermaar.observability import close_logging

        with TemporaryDirectory(prefix="nahormaar-contract-app-") as temporary:
            root = Path(temporary)
            access = root / "access.toml"
            access.write_text('owner_id = "1"\nadmin_ids = []\n', encoding="utf-8")
            application = bootstrap(
                {
                    "DATABASE_URL": (
                        "postgresql+psycopg://contract:contract@127.0.0.1:1/contract"
                    ),
                    "PUBLIC_ORIGIN": "http://localhost:3000",
                    "ACCESS_PATH": str(access),
                    "NAHORMAAR_LOG_DIR": str(root / "logs"),
                    "DISCORD_TOKEN": "",
                }
            )
            try:
                app = create_app(application)
                schema = app.openapi()
                validate_operation_ids(schema)
                args.schema.write_text(
                    json.dumps(schema, sort_keys=True),
                    encoding="utf-8",
                )
            finally:
                asyncio.run(application.close())
                close_logging()
        return

    python = (
        ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    node = shutil.which("node")
    generator = ROOT / "node_modules/openapi-typescript/bin/cli.js"
    if not python.is_file() or not node or not generator.is_file():
        raise SystemExit("Run python scripts/dev.py setup before generating API types.")
    with TemporaryDirectory(prefix="nahormaar-contract-") as temporary:
        schema = Path(temporary) / "openapi.json"
        output = Path(temporary) / "api.ts"
        subprocess.run(  # noqa: S603 - fixed local tools and temporary paths
            [str(python), __file__, "--schema", str(schema)],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed local generator, no remote schema
            [node, str(generator), str(schema), "--alphabetize", "-o", str(output)],
            cwd=ROOT,
            check=True,
        )
        generated = TYPESCRIPT_SPDX_HEADER + output.read_text(encoding="utf-8")
        if args.check:
            if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != generated:
                raise SystemExit(
                    "API types are stale. Run python scripts/nahoermaar_api_contract.py."
                )
            print("API types match the backend schema.")
        else:
            TARGET.write_text(generated, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

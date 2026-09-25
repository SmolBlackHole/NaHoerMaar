# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Generate or check the nahoermaar client contract without starting services."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend/app/core/api/schema.generated.ts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--schema", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.schema:
        import asyncio

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
                args.schema.write_text(
                    json.dumps(app.openapi(), sort_keys=True),
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
        generated = output.read_text(encoding="utf-8")
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

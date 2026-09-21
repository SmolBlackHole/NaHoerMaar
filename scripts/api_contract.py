# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Generate or check the frontend API types without starting any services."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "frontend/shared/api.generated.ts"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--schema", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.schema:
        from contextlib import AbstractAsyncContextManager

        from nahormaar_backend.engine.api import create_app
        from nahormaar_backend.engine.runtime import Services

        def no_runtime() -> AbstractAsyncContextManager[Services]:
            raise RuntimeError("Schema export must not start the application lifespan.")

        app = create_app(no_runtime, public_origin="http://localhost")
        args.schema.write_text(json.dumps(app.openapi()), encoding="utf-8")
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
                    "API types are stale. Run npm run api:generate --workspace frontend."
                )
            print("API types match the backend schema.")
        else:
            TARGET.write_text(generated, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import ast
from importlib.util import resolve_name
from pathlib import Path

_PACKAGE = Path(__file__).parents[2] / "src" / "nahoermaar"
_WRITE_MODULES = frozenset({"catalog", "listening", "player", "users"})


def test_write_modules_do_not_import_foreign_repositories() -> None:
    violations: list[str] = []

    for owner in sorted(_WRITE_MODULES):
        for path in sorted((_PACKAGE / owner).rglob("*.py")):
            package = _package_name(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported in _imports(tree, package):
                parts = imported.split(".")
                if (
                    len(parts) >= 3
                    and parts[0] == "nahoermaar"
                    and parts[1] in _WRITE_MODULES - {owner}
                    and parts[2] == "repository"
                ):
                    violations.append(
                        f"{path.relative_to(_PACKAGE)} imports {imported}"
                    )

    assert not violations, "Write-module repository imports:\n" + "\n".join(violations)


def _package_name(path: Path) -> str:
    relative_parent = path.relative_to(_PACKAGE).parent
    suffix = ".".join(relative_parent.parts)
    return "nahoermaar" if not suffix else f"nahoermaar.{suffix}"


def _imports(tree: ast.AST, package: str) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            name = f"{'.' * node.level}{node.module}"
            imported.append(resolve_name(name, package) if node.level else name)
    return tuple(imported)

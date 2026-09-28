# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import ast
from importlib.util import resolve_name
from pathlib import Path

_PACKAGE = Path(__file__).parents[2] / "src" / "nahoermaar"
_FEATURE_MODULES = frozenset(
    {
        "catalog",
        "integrations",
        "listening",
        "operations",
        "player",
        "statistics",
        "users",
        "views",
    }
)
_MODULE_FACTORIES = frozenset(
    {
        "complete_integrations_module",
        "complete_operations_module",
        "complete_player_module",
        "create_catalog_module",
        "create_listening_module",
        "create_operations_foundation",
        "create_statistics_module",
        "create_users_module",
        "create_views_module",
        "prepare_integrations_module",
        "prepare_player_module",
    }
)
_APPLICATION_FIELDS = frozenset(
    {
        "settings",
        "database",
        "bus",
        "users",
        "catalog",
        "player",
        "listening",
        "statistics",
        "views",
        "integrations",
        "operations",
    }
)


def test_bootstrap_imports_features_only_through_composition_entries() -> None:
    path = _PACKAGE / "bootstrap.py"
    imports = _imports(_tree(path), "nahoermaar")
    violations: list[str] = []

    for imported in imports:
        parts = imported.split(".")
        if len(parts) < 2 or parts[0] != "nahoermaar":
            continue
        if parts[1] not in _FEATURE_MODULES:
            continue
        entry = f"nahoermaar.{parts[1]}.main"
        if imported != entry and not imported.startswith(f"{entry}."):
            violations.append(imported)

    assert not violations, "Bootstrap bypasses module entries:\n" + "\n".join(
        sorted(violations)
    )


def test_feature_modules_do_not_import_foreign_repositories() -> None:
    violations: list[str] = []

    for owner in sorted(_FEATURE_MODULES - {"views"}):
        for path in sorted((_PACKAGE / owner).rglob("*.py")):
            package = _package_name(path)
            for imported in _imports(_tree(path), package):
                parts = imported.split(".")
                if (
                    len(parts) >= 3
                    and parts[0] == "nahoermaar"
                    and parts[1] in _FEATURE_MODULES - {owner}
                    and parts[2] == "repository"
                ):
                    violations.append(
                        f"{path.relative_to(_PACKAGE)} imports {imported}"
                    )

    assert not violations, "Foreign repository imports:\n" + "\n".join(violations)


def test_package_initializers_have_no_construction_side_effects() -> None:
    violations: list[str] = []

    for path in sorted(_PACKAGE.rglob("__init__.py")):
        package = _package_name(path)
        tree = _tree(path)
        for statement in tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                continue
            if _is_module_docstring(statement) or _is_all_assignment(statement):
                continue
            violations.append(
                f"{path.relative_to(_PACKAGE)}:{statement.lineno} "
                f"contains {type(statement).__name__}"
            )
        for imported in _imports(tree, package):
            if imported.endswith(".main") or ".main." in imported:
                violations.append(
                    f"{path.relative_to(_PACKAGE)} imports composition entry {imported}"
                )

    assert not violations, "Package initializer side effects:\n" + "\n".join(violations)


def test_lifecycle_resources_are_created_only_by_their_owner() -> None:
    violations: list[str] = []

    for path in sorted(_PACKAGE.rglob("*.py")):
        for call in (
            node for node in ast.walk(_tree(path)) if isinstance(node, ast.Call)
        ):
            if _call_name(call) != "LifecycleResource":
                continue
            relative = path.relative_to(_PACKAGE)
            if relative != Path("bootstrap.py") and relative.name != "main.py":
                violations.append(f"{relative}:{call.lineno}")

    assert not violations, (
        "Lifecycle resources without one module owner:\n" + "\n".join(violations)
    )


def test_application_is_only_process_infrastructure_and_typed_modules() -> None:
    path = _PACKAGE / "bootstrap.py"
    application = next(
        node
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef) and node.name == "Application"
    )
    public_fields = {
        target.id
        for statement in application.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance((target := statement.target), ast.Name)
        and not target.id.startswith("_")
    }

    assert public_fields == _APPLICATION_FIELDS


def test_module_factories_have_only_one_process_initialization_path() -> None:
    violations: list[str] = []

    for path in sorted(_PACKAGE.rglob("*.py")):
        relative = path.relative_to(_PACKAGE)
        for call in (
            node for node in ast.walk(_tree(path)) if isinstance(node, ast.Call)
        ):
            if _call_name(call) in _MODULE_FACTORIES and relative != Path(
                "bootstrap.py"
            ):
                violations.append(f"{relative}:{call.lineno} calls {_call_name(call)}")

    assert not violations, "Parallel module initialization paths:\n" + "\n".join(
        violations
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_name(path: Path) -> str:
    relative_parent = path.relative_to(_PACKAGE).parent
    suffix = ".".join(relative_parent.parts)
    return "nahoermaar" if not suffix else f"nahoermaar.{suffix}"


def _imports(tree: ast.AST, package: str) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            name = f"{'.' * node.level}{module}"
            resolved = resolve_name(name, package) if node.level else module
            imported.append(resolved)
            imported.extend(f"{resolved}.{alias.name}" for alias in node.names)
    return tuple(imported)


def _is_module_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_all_assignment(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Assign) and all(
        isinstance(target, ast.Name) and target.id == "__all__"
        for target in statement.targets
    )


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Validate repository text hygiene and the public Markdown link graph."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import deque
from pathlib import Path
from urllib.parse import unquote

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".build",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        ".nuxt",
        ".output",
        "build",
        "dist",
        "node_modules",
        "templates",
    }
)
TEXT_FILENAMES = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "CMakeLists.txt",
        "Dockerfile",
        "Dockerfile.ci",
        "LICENSE",
    }
)
EXCLUDED_FILENAMES = frozenset(
    {"TODO.md", "TODO_PAUSED.md", "refactor.md", "refactoring.md"}
)
TEXT_SUFFIXES = frozenset(
    {
        ".cff",
        ".cpp",
        ".hpp",
        ".js",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".toml",
        ".txt",
        ".yml",
        ".yaml",
    }
)
MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")
URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
TEMPLATE_TOKEN = re.compile(r"{{[a-z0-9_]+}}")


def _excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if relative.parent == Path() and relative.name in EXCLUDED_FILENAMES:
        return True
    if relative.parts[0] in {"data", "tmp"}:
        return True
    return any(
        part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info")
        for part in relative.parts
    )


def _text_files(root: Path) -> tuple[Path, ...]:
    files = (
        path
        for path in root.rglob("*")
        if path.is_file()
        and not _excluded(path, root)
        and (path.name in TEXT_FILENAMES or path.suffix.lower() in TEXT_SUFFIXES)
    )
    return tuple(sorted(files))


def _markdown_pages(root: Path) -> tuple[Path, ...]:
    return tuple(
        path.relative_to(root)
        for path in _text_files(root)
        if path.suffix.lower() == ".md"
    )


def _markdown_targets(document: Path) -> tuple[str, ...]:
    targets: list[str] = []
    in_fence = False
    for line in document.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            targets.extend(MARKDOWN_LINK.findall(line))
    if in_fence:
        raise ValueError(f"unclosed Markdown fence: {document}")
    return tuple(targets)


def _local_markdown_target(root: Path, source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        if closing == -1:
            return None
        target = target[1:closing]
    else:
        target = target.partition(" ")[0]
    if not target or target.startswith("#") or URL_SCHEME.match(target):
        return None
    target = unquote(target.partition("#")[0])
    if not target:
        return None
    candidate = (root / source.parent / target).resolve()
    if candidate.is_dir():
        candidate /= "README.md"
    if candidate.suffix.lower() != ".md":
        return None
    try:
        return candidate.relative_to(root.resolve())
    except ValueError:
        return Path("..") / candidate.name


def _check_markdown_graph(root: Path) -> list[str]:
    pages = _markdown_pages(root)
    page_set = frozenset(pages)
    edges: dict[Path, set[Path]] = {page: set() for page in pages}
    errors: list[str] = []

    for source in pages:
        for raw_target in _markdown_targets(root / source):
            target = _local_markdown_target(root, source, raw_target)
            if target is None:
                continue
            if target not in page_set:
                errors.append(f"broken Markdown link: {source} -> {raw_target}")
            else:
                edges[source].add(target)

    root_document = Path("README.md")
    if root_document not in page_set:
        errors.append("missing documentation root: README.md")
        return errors

    reachable = {root_document}
    queue = deque((root_document,))
    while queue:
        source = queue.popleft()
        for target in edges[source]:
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    for page in sorted(page_set - reachable):
        errors.append(f"unreachable Markdown page: {page}")
    return errors


def _check_text_files(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _text_files(root):
        contents = path.read_bytes()
        relative = path.relative_to(root)
        if contents and not contents.endswith(b"\n"):
            errors.append(f"missing final newline: {relative}")
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"not UTF-8: {relative}")
            continue
        if TEMPLATE_TOKEN.search(text):
            errors.append(f"unresolved template token: {relative}")
    return errors


def _check_git_diff(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []
    git = shutil.which("git")
    if git is None:
        return ["git diff --check failed: Git is not installed"]
    completed = subprocess.run(  # noqa: S603 - installed Git, fixed read-only arguments
        (git, "diff", "--check"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return []
    details = (completed.stdout + completed.stderr).strip()
    return [f"git diff --check failed: {details}"]


def repository_errors(root: Path) -> tuple[str, ...]:
    """Return all deterministic repository validation failures."""
    resolved = root.resolve()
    return tuple(
        [
            *_check_text_files(resolved),
            *_check_markdown_graph(resolved),
            *_check_git_diff(resolved),
        ]
    )


def run_repository_checks(root: Path) -> None:
    """Print a compact result and exit on validation failures."""
    errors = repository_errors(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Repository checks passed.")


if __name__ == "__main__":
    run_repository_checks(Path(__file__).resolve().parents[1])

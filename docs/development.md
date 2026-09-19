# Develop NaHörMaar

Parent: [Project README](../README.md)

Python 3.12+ and Node.js 22+ with npm are required.

## Set up

From the repository root:

```powershell
python scripts/dev.py setup
```

Linux and macOS can also use `./scripts/setup.sh`. If PowerShell blocks
`scripts/setup.ps1`, use the Python command above.

## Run checks

```powershell
python scripts/dev.py check
```

This runs:

- Repository text and documentation link checks.
- Ruff linting and formatting, strict mypy and Pyright, and pytest for Python.
- Node.js syntax checks and tests for the frontend.

Use `npm run check` to check only the frontend. Shell wrappers are available as
`scripts/check.sh` and `scripts/check.ps1`.

GitHub Actions runs the same checks on Windows and Linux.

## Editor

Open the repository root in VS Code. Use **Python: Select Interpreter** to select
`.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS.

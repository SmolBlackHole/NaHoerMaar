# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Source -3 (Join-Path $PSScriptRoot "dev.py") setup
        exit $LASTEXITCODE
    }
}
if (-not $pythonCommand) {
    throw "Python 3.12 or newer is required for repository tooling."
}

& $pythonCommand.Source (Join-Path $PSScriptRoot "dev.py") setup
exit $LASTEXITCODE

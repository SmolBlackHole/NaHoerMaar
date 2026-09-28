# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $repositoryRoot
try {
    & docker build `
        --file docker/Dockerfile `
        --target backend `
        --tag nahormaar-backend:local `
        .
    if ($LASTEXITCODE -ne 0) {
        throw "The backend image build failed."
    }

    & docker build `
        --file docker/Dockerfile `
        --target frontend `
        --tag nahormaar-frontend:local `
        .
    if ($LASTEXITCODE -ne 0) {
        throw "The frontend image build failed."
    }

    & docker compose up -d --no-build
    if ($LASTEXITCODE -ne 0) {
        throw "The Docker Compose stack failed to start."
    }
}
finally {
    Pop-Location
}

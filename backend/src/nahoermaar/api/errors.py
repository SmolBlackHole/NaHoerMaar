# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Stable error documents shared by HTTP middleware and exception handlers."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorView(BaseModel):
    """One machine-readable API failure."""

    model_config = ConfigDict(extra="forbid")

    error: str
    retryable: bool | None = None


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorView, "description": "Invalid request"},
    401: {"model": ErrorView, "description": "Authentication required"},
    403: {"model": ErrorView, "description": "Request not allowed"},
    404: {"model": ErrorView, "description": "Resource not found"},
    409: {"model": ErrorView, "description": "State conflict"},
    422: {"model": ErrorView, "description": "Validation failed"},
    503: {"model": ErrorView, "description": "Dependency unavailable"},
}

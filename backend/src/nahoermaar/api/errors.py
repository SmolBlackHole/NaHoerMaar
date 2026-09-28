# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Stable error documents shared by HTTP middleware and exception handlers."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorView(BaseModel):
    """One machine-readable API failure."""

    model_config = ConfigDict(extra="forbid")

    code: str
    retryable: bool | None = None


class ApiErrorCode(StrEnum):
    """Stable failures owned by the HTTP boundary rather than one domain."""

    ACCESS_DENIED = "access_denied"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AVATAR_UNAVAILABLE = "avatar_unavailable"
    CONFLICT = "conflict"
    GATEWAY_TIMEOUT = "gateway_timeout"
    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    REQUEST_TIMEOUT = "request_timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    UPSTREAM_FAILED = "upstream_failed"
    VALIDATION_FAILED = "validation_failed"


class ApiError(RuntimeError):
    """Expected HTTP-boundary failure with a stable public code."""

    def __init__(
        self,
        code: ApiErrorCode,
        status: int,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = status
        self.retryable = retryable


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorView, "description": "Invalid request"},
    401: {"model": ErrorView, "description": "Authentication required"},
    403: {"model": ErrorView, "description": "Request not allowed"},
    404: {"model": ErrorView, "description": "Resource not found"},
    405: {"model": ErrorView, "description": "Method not allowed"},
    409: {"model": ErrorView, "description": "State conflict"},
    422: {"model": ErrorView, "description": "Validation failed"},
    429: {"model": ErrorView, "description": "Too many requests"},
    500: {"model": ErrorView, "description": "Internal server error"},
    502: {"model": ErrorView, "description": "Upstream dependency failed"},
    503: {"model": ErrorView, "description": "Dependency unavailable"},
    504: {"model": ErrorView, "description": "Upstream dependency timed out"},
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Select the public errors one operation can actually return."""
    return {
        status: {
            **ERROR_RESPONSES[status],
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorView"}
                }
            },
        }
        for status in statuses
    }

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

from pathlib import Path

from fastapi import Request
import pytest

from nahoermaar.api.auth import _browser_origin  # pyright: ignore[reportPrivateUsage]
from nahoermaar.config import AuthSettings
from nahoermaar.users.domain import AuthError, AuthErrorCode


def _request(origin: str | None = None) -> Request:
    headers = (
        [] if origin is None else [(b"x-nahormaar-browser-origin", origin.encode())]
    )
    return Request({"type": "http", "headers": headers})


def test_browser_origin_accepts_configured_origin_and_has_canonical_fallback() -> None:
    settings = AuthSettings(
        "http://localhost:3000",
        "123",
        "secret",
        Path("access.toml"),
        frozenset({"http://localhost:3001", "https://tunnel.example.test"}),
    )

    assert _browser_origin(_request(), settings) == "http://localhost:3000"
    assert _browser_origin(_request("http://localhost:3001"), settings) == (
        "http://localhost:3001"
    )
    assert _browser_origin(_request("https://tunnel.example.test"), settings) == (
        "https://tunnel.example.test"
    )


def test_browser_origin_rejects_unconfigured_origin() -> None:
    settings = AuthSettings(
        "http://localhost:3000",
        "123",
        "secret",
        Path("access.toml"),
    )

    with pytest.raises(AuthError) as error:
        _browser_origin(_request("https://evil.example"), settings)

    assert error.value.code is AuthErrorCode.ORIGIN_FORBIDDEN
    assert error.value.status == 403

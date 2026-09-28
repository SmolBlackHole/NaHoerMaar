# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio

from fastapi import FastAPI
import httpx

from nahoermaar.api.app import install_error_handlers
from nahoermaar.api.errors import ApiError, ApiErrorCode


def test_http_boundary_returns_stable_error_documents() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/resource")
    async def resource() -> dict[str, bool]:
        return {"available": True}

    @app.get("/validated")
    async def validated(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/avatar")
    async def avatar() -> None:
        raise ApiError(ApiErrorCode.AVATAR_UNAVAILABLE, 404)

    @app.get("/broken")
    async def broken() -> None:
        raise RuntimeError("private implementation detail")

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            missing = await client.get("/missing")
            assert missing.status_code == 404
            assert missing.json() == {"code": "not_found", "retryable": False}

            wrong_method = await client.post("/resource")
            assert wrong_method.status_code == 405
            assert wrong_method.json() == {
                "code": "method_not_allowed",
                "retryable": False,
            }

            invalid = await client.get("/validated", params={"value": "nope"})
            assert invalid.status_code == 422
            assert invalid.json() == {"code": "validation_failed"}

            unavailable = await client.get("/avatar")
            assert unavailable.status_code == 404
            assert unavailable.json() == {
                "code": "avatar_unavailable",
                "retryable": False,
            }

            unexpected = await client.get("/broken")
            assert unexpected.status_code == 500
            assert unexpected.json() == {
                "code": "internal_error",
                "retryable": True,
            }
            assert "private implementation detail" not in unexpected.text

    asyncio.run(scenario())

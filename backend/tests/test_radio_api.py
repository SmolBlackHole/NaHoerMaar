# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from pathlib import Path
from uuid import uuid4

from nahormaar_backend.application.radio import RadioCatalog
from test_api import Harness, headers, mutation
from test_commands import wait_for
from test_radio import Recommendations


def test_radio_api_auth_preview_start_replay_and_end(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = Harness(
            tmp_path / "radio.sqlite3", radio_catalog=RadioCatalog(Recommendations())
        )
        async with harness.client() as client:
            assert harness.controller is not None
            controller = harness.controller
            body = {
                "kind": "track",
                "source_url": "https://youtu.be/Pqp9fDRp1lw",
                "title": "Hazy Mercer",
            }
            preview = await client.post(
                "/api/radio/preview", json=body, headers=headers()
            )
            assert preview.status_code == 200, preview.text
            assert len(preview.json()["entries"]) == 12
            assert harness.controller.snapshot.upcoming == ()
            invalid = await client.post(
                "/api/radio/preview",
                json={**body, "source_url": "not a link"},
                headers=headers(),
            )
            assert invalid.status_code == 422
            key = headers()
            start = {"preview_id": preview.json()["id"], "expected_session_id": None}
            first = mutation(
                await client.post("/api/radio/start", json=start, headers=key)
            )
            assert first.code == "ok"
            assert first.snapshot.radio.initiator is not None
            assert first.snapshot.radio.initiator.name == "Andrey"
            await wait_for(lambda: len(controller.snapshot.upcoming) == 3)
            repeated = mutation(
                await client.post("/api/radio/start", json=start, headers=key)
            )
            assert repeated.replayed
            assert repeated.snapshot.radio.session_id == first.snapshot.radio.session_id
            conflict = mutation(
                await client.post(
                    "/api/radio/stop",
                    json={"expected_session_id": str(uuid4())},
                    headers=headers(),
                ),
                409,
            )
            assert conflict.code == "radio_conflict"
            ended = mutation(
                await client.post(
                    "/api/radio/stop",
                    json={"expected_session_id": str(first.snapshot.radio.session_id)},
                    headers=headers(),
                )
            )
            assert ended.snapshot.radio.state == "off"
            assert len(ended.snapshot.upcoming) == 3
            assert all(entry.origin == "radio" for entry in ended.snapshot.upcoming)
            expired = mutation(
                await client.post(
                    "/api/radio/start",
                    json={"preview_id": str(uuid4()), "expected_session_id": None},
                    headers=headers(),
                ),
                410,
            )
            assert expired.code == "radio_preview_expired"
            client.cookies.clear()
            denied = await client.post(
                "/api/radio/preview", json=body, headers=headers()
            )
            assert denied.status_code == 401

    asyncio.run(scenario())

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Integrity checks for independent examples, not running API integration tests."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

FIXTURES = Path(__file__).with_name("fixtures")
WIRE: dict[str, Any] = json.loads((FIXTURES / "wire.json").read_text(encoding="utf-8"))
BODIES: dict[str, Any] = WIRE["bodies"]
STATE_FIELDS = {
    "revision",
    "queue_revision",
    "state",
    "current",
    "upcoming",
    "recently_played",
    "voice_state",
    "channel_id",
    "playback_id",
    "volume",
    "crossfade_seconds",
    "position_seconds",
    "position_updated_at",
    "last_issue",
    "radio",
}
ENTRY_FIELDS = {
    "id",
    "source_url",
    "video_id",
    "title",
    "uploader",
    "duration_seconds",
    "thumbnail_url",
    "artist",
    "uploader_url",
    "added_by",
    "origin",
}


@pytest.mark.parametrize(
    "name", ["idle", "playing", "paused", "radio_waiting", "playback_issue"]
)
def test_state_fixtures_keep_the_public_shape_and_safe_values(name: str) -> None:
    state = BODIES[name]
    assert set(state) == STATE_FIELDS
    assert state["revision"] >= state["queue_revision"] >= 0
    assert 0 <= state["volume"] <= 1
    assert state["crossfade_seconds"] in {0, 3, 4, 5, 6, 7}
    assert state["channel_id"] is None or state["channel_id"].isdigit()
    if state["playback_id"]:
        UUID(state["playback_id"])
    if state["position_updated_at"]:
        assert (
            datetime.fromisoformat(state["position_updated_at"]).utcoffset() is not None
        )
    entries = list(state["upcoming"])
    if state["current"]:
        entries.append(state["current"])
    entries.extend(item["entry"] for item in state["recently_played"])
    for entry in entries:
        assert set(entry) == ENTRY_FIELDS
        UUID(entry["id"])
        assert entry["origin"] in {"manual", "radio"}
        if entry["added_by"]:
            assert set(entry["added_by"]) == {"id", "name", "avatar"}
    radio = state["radio"]
    assert set(radio) == {
        "state",
        "session_id",
        "seed",
        "initiator",
        "error",
        "event_id",
        "action",
        "actor",
    }


def test_pause_and_issue_do_not_create_another_history_record() -> None:
    playing, paused = BODIES["playing"], BODIES["paused"]
    assert playing["state"] == "playing" and paused["state"] == "paused"
    assert playing["playback_id"] == paused["playback_id"]
    assert playing["position_seconds"] == paused["position_seconds"] == 42.5
    assert playing["recently_played"] == paused["recently_played"]
    assert BODIES["playback_issue"]["recently_played"] == paused["recently_played"]
    assert (
        len(
            {
                playing["playback_id"],
                playing["current"]["id"],
                playing["recently_played"][0]["id"],
            }
        )
        == 3
    )


def test_mutation_examples_retain_actor_ids_original_entries_and_twelve_second_undo() -> (
    None
):
    added, removed, restored, replayed = (
        BODIES[name] for name in ("added", "removed", "restored", "replayed")
    )
    assert added["entry_id"] == added["entries"][0]["id"]
    assert added["entries"][0] == added["snapshot"]["upcoming"][-1]
    assert added["entries"][0]["title"] is None
    assert removed["removed_count"] == restored["restored_count"] == 1
    assert removed["entries"] == restored["entries"] == replayed["entries"]
    assert restored["snapshot"]["upcoming"] == restored["entries"]
    assert replayed["replayed"] and replayed["request_id"] == removed["request_id"]
    assert replayed["actor"] == removed["actor"]
    assert replayed["snapshot"]["revision"] > removed["snapshot"]["revision"]
    expires = datetime.fromisoformat(removed["undo_expires_at"])
    removed_at = datetime.fromisoformat("2026-09-21T12:00:42.500000Z")
    assert (expires - removed_at).total_seconds() == 12
    assert (
        added["snapshot"]["upcoming"][0]["source_url"]
        == added["entries"][0]["source_url"]
    )
    assert added["snapshot"]["upcoming"][0]["id"] != added["entries"][0]["id"]


def test_paused_seek_changes_attempt_but_not_current_entry_or_logical_play() -> None:
    before = BODIES["paused"]
    after = BODIES["seeked"]["snapshot"]
    assert after["state"] == "paused"
    assert after["position_seconds"] == 75
    assert after["playback_id"] != before["playback_id"]
    assert after["current"] == before["current"]
    assert after["recently_played"] == before["recently_played"]
    assert after["queue_revision"] == before["queue_revision"]
    assert BODIES["playback_conflict"]["code"] == "playback_conflict"


def test_discovery_versions_and_occurrences_remain_distinct() -> None:
    search = BODIES["search_stale"]
    assert len(search["snapshot_id"]) == len(search["latest_snapshot_id"]) == 32
    assert search["snapshot_id"] != search["latest_snapshot_id"]
    assert search["next_offset"] == 10
    next_page = BODIES["search_next"]
    assert next_page["snapshot_id"] == search["snapshot_id"]
    assert next_page["next_offset"] is None
    assert [
        entry["index"] for entry in search["entries"] + next_page["entries"]
    ] == list(range(11))
    playlist = BODIES["playlist_ready"]
    first, unavailable, duplicate = playlist["entries"]
    assert [entry["index"] for entry in playlist["entries"]] == [0, 1, 2]
    assert first["source_url"] == duplicate["source_url"]
    assert unavailable["source_url"] is None and unavailable["unavailable"]
    assert playlist["refreshing"] and playlist["state"] == "ready"
    assert BODIES["radio_preview"]["seed"] == BODIES["radio_waiting"]["radio"]["seed"]


def test_http_cases_reference_real_bodies_and_preserve_error_envelopes() -> None:
    assert WIRE["common_response_headers"]["cache-control"] == "no-store"
    names: set[str] = set()
    for case in WIRE["cases"]:
        assert case["name"] not in names
        names.add(case["name"])
        request, response = case["request"], case["response"]
        assert request["path"].startswith("/api/")
        assert response["body"] in BODIES
        if request["method"] in {"POST", "DELETE", "PUT"}:
            UUID(request["headers"]["Idempotency-Key"])
        if request["method"] in {"GET", "DELETE"}:
            assert "json" not in request
    statuses = {
        case["response"]["body"]: case["response"]["status"] for case in WIRE["cases"]
    }
    assert statuses["playlist_ready"] == 202
    assert statuses["queue_conflict"] == 409
    assert statuses["undo_unavailable"] == statuses["search_expired"] == 410
    assert statuses["signed_out"] == 401
    assert BODIES["queue_conflict"]["code"] == "queue_conflict"
    assert BODIES["undo_unavailable"]["code"] == "undo_unavailable"
    assert BODIES["signed_out"] == {"code": "signed_out"}
    assert set(BODIES["search_expired"]) == {"detail"}
    channel = BODIES["channels"][0]
    assert isinstance(channel["id"], str) and isinstance(channel["guild_id"], str)
    assert {"guild_name", "can_connect", "can_speak"} <= channel.keys()


def test_sse_fixture_starts_with_current_snapshot_and_finishes_with_auth_event() -> (
    None
):
    frames = (
        (FIXTURES / "reconnect.sse").read_text(encoding="utf-8").strip().split("\n\n")
    )
    events = [
        dict(line.split(": ", 1) for line in frame.splitlines()) for frame in frames
    ]
    state, auth = events
    assert state["event"] == "state" and state["id"] == str(
        BODIES["playing"]["revision"]
    )
    assert json.loads(state["data"]) == BODIES["playing"]
    assert state["id"] != WIRE["sse"]["request"]["headers"]["Last-Event-ID"]
    assert auth["event"] == "auth" and "id" not in auth
    assert json.loads(auth["data"]) == BODIES["signed_out"]
    assert WIRE["sse"]["close_after_auth"]


def test_public_examples_never_include_temporary_source_or_transport_fields() -> None:
    def check(value: object) -> None:
        if isinstance(value, dict):
            assert (
                not {"stream_url", "headers", "is_opus", "error_trace"} & value.keys()
            )
            for nested in cast(dict[str, object], value).values():
                check(nested)
        elif isinstance(value, list):
            for nested in cast(list[object], value):
                check(nested)

    check(BODIES)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
import http.client
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from starlette.types import Message, Receive, Scope, Send
from sqlalchemy import update
from sqlalchemy.orm import Session as DatabaseSession

from nahormaar_backend.application.auth import Auth, SESSION_COOKIE, csrf_token, digest
from nahormaar_backend.application.access import Access, Operators
from nahormaar_backend.config import AuthSettings
from nahormaar_backend.domain.access import AccessRole, DiscordMember
from nahormaar_backend.domain.identity import DiscordIdentity
from nahormaar_backend.persistence.database import database_engine as account_engine
from nahormaar_backend.persistence.models import SessionRow
from nahormaar_backend.engine.api import create_app, event_stream
from nahormaar_backend.engine.audio import PlayableSource, VoiceChannel
from nahormaar_backend.engine.domain.catalog import (
    MediaKind,
    MediaReference,
    PlaylistPage,
    TrackFinding,
    TrackPage,
)
from nahormaar_backend.engine.domain.metadata import TrackMetadata
from nahormaar_backend.engine.domain.tracks import MediaIdentity
from nahormaar_backend.engine.persistence import database_engine
from nahormaar_backend.engine.runtime import Services, open_engine
from nahormaar_backend.engine.schema import upgrade
from nahormaar_backend.engine.youtube import YouTubeProvider
from .database import database_url
from .test_catalog import TIME
from .test_playback_session import ControlledOutput, ControlledVoice
from .test_radio_session import until

TOKEN = "a" * 43
DISCORD_ID = "243718053362270208"


class Identity:
    async def authorization_url(self, state: str, verifier: str) -> str:
        return f"https://discord.invalid/authorize?state={state}"

    async def identity(self, code: str, verifier: str) -> DiscordIdentity:
        return DiscordIdentity(DISCORD_ID, "Listener")


class Provider(YouTubeProvider):
    key = "youtube_music"

    def __init__(self) -> None:
        super().__init__(Path("unused"))
        self.finding = TrackFinding(
            MediaReference(
                MediaIdentity("youtube", "abcdefghijk"),
                MediaKind.TRACK,
                "https://music.youtube.com/watch?v=abcdefghijk",
            ),
            TrackMetadata(title="Амура", duration_seconds=120),
        )
        self.calls = 0
        self.playlist_title = "Fixture"
        self.closed = False

    async def track(self, identity: MediaIdentity) -> TrackFinding:
        return self.finding

    async def search(
        self, query: str, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        self.calls += 1
        return TrackPage((self.finding,))

    async def playlist(
        self, identity: MediaIdentity, *, limit: int, continuation: str | None = None
    ) -> PlaylistPage:
        return PlaylistPage(
            MediaReference(
                identity,
                MediaKind.PLAYLIST,
                f"https://music.youtube.com/playlist?list={identity.external_id}",
            ),
            self.playlist_title,
            TrackPage((self.finding, self.finding)),
        )

    async def radio_next(
        self, seed: MediaReference, *, limit: int, continuation: str | None = None
    ) -> TrackPage:
        return TrackPage((self.finding,))

    async def resolve_audio(self, identity: MediaIdentity) -> PlayableSource:
        return PlayableSource(
            self.finding, "https://stream.invalid/private", is_opus=True
        )

    async def close(self) -> None:
        self.closed = True
        await super().close()


class Voice(ControlledVoice):
    def channels(self) -> tuple[VoiceChannel, ...]:
        return (VoiceChannel(123, "General", 456, "Fixture server", True, True),)


class Directory:
    def members(self) -> tuple[DiscordMember, ...]:
        return (
            DiscordMember(
                "3",
                "kai",
                "Kai",
                "https://cdn.invalid/avatar.png",
                "456",
                "Fixture server",
            ),
            DiscordMember(
                DISCORD_ID,
                "listener",
                "Listener on Discord",
                "https://cdn.invalid/listener.png",
                "456",
                "Fixture server",
            ),
            DiscordMember(
                DISCORD_ID,
                "listener",
                "Listener elsewhere",
                "https://cdn.invalid/listener.png",
                "789",
                "Second server",
            ),
        )


@asynccontextmanager
async def fixture(
    tmp_path: Path,
) -> AsyncGenerator[tuple[httpx.AsyncClient, Services, Provider, ControlledOutput]]:
    path, access_path = tmp_path / "engine.db", tmp_path / "access.toml"
    access_path.write_text('owner_id = "9"', encoding="utf-8")
    engine = database_engine(database_url(path))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
    finally:
        await engine.dispose()
    auth = Auth(
        AuthSettings(
            "http://localhost",
            "",
            "",
            database_url(path),
            access_path,
        ),
        ("0001",),
        access=Access(database_url(path), Operators("9", ())),
        provider=Identity(),
        clock=lambda: TIME.timestamp(),
    )
    await auth.access.grant("9", DISCORD_ID)
    auth.accounts.create_session(
        DISCORD_ID,
        "Listener",
        "0001",
        AccessRole.USER,
        digest(TOKEN),
        TIME.timestamp() + 3600,
        TIME.timestamp(),
        None,
    )
    provider, audio, voice = Provider(), ControlledOutput(), Voice()
    async with open_engine(
        uuid4(),
        auth=auth,
        providers=(provider,),
        audio=audio,
        voice=voice,
        directory=Directory(),
        clock=lambda: TIME,
    ) as services:

        @asynccontextmanager
        async def borrow() -> AsyncGenerator[Services]:
            yield services

        app = create_app(borrow, public_origin="http://localhost")
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                cookies={SESSION_COOKIE: TOKEN},
                headers={
                    "origin": "http://localhost",
                    "x-csrf-token": csrf_token(TOKEN),
                },
            ) as client:
                yield client, services, provider, audio
    assert provider.closed and audio.progress is None and voice.connection is None


def test_health_routes_are_public_and_distinguish_startup(tmp_path: Path) -> None:
    async def scenario() -> None:
        @asynccontextmanager
        async def unopened() -> AsyncGenerator[Services]:
            raise AssertionError("Health requests must not open the engine runtime.")
            yield

        app = create_app(unopened, public_origin="http://localhost")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            assert (
                await client.get("/healthz", headers={"host": "backend"})
            ).status_code == 200
            assert (
                await client.get("/healthz", headers={"host": "untrusted.invalid"})
            ).status_code == 400
            waiting = await client.get("/readyz")
            assert waiting.status_code == 503
            assert waiting.json() == {"status": "starting"}

        async with fixture(tmp_path) as (client, _services, _provider, _audio):
            client.cookies.clear()
            ready = await client.get("/readyz")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ready"}

    asyncio.run(scenario())


def test_admin_log_tail_requires_role_and_returns_recent_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, _provider, _audio):
            logger = logging.getLogger("nahormaar_backend.engine.test_logs")
            previous_level = logger.level
            logger.setLevel(logging.INFO)
            try:
                assert (await client.get("/api/diagnostics/logs")).status_code == 403
                services.access.operators = Operators(DISCORD_ID, ())
                assert (await client.get("/api/auth/session")).json()[
                    "is_admin"
                ] is True
                logger.warning("engine.test.marker")
                response = await client.get("/api/diagnostics/logs")
                assert response.status_code == 200
                entries = response.json()["entries"]
                marker = next(
                    entry
                    for entry in entries
                    if entry["message"] == "engine.test.marker"
                )
                assert marker["level"] == "WARNING"
                assert marker["source"] == "engine.test_logs"
                assert (
                    await client.get(f"/api/diagnostics/logs?after={marker['id']}")
                ).json() == {"entries": []}
            finally:
                logger.setLevel(previous_level)

    asyncio.run(scenario())


def test_access_admin_api_manages_grants_history_and_member_directory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, _provider, _audio):
            assert (await client.get("/api/admin/access")).status_code == 403
            services.access.operators = Operators(DISCORD_ID, ())
            state = (await client.get("/api/admin/access")).json()
            assert state["owner_id"] == DISCORD_ID
            assert state["grants"] == []
            members = (await client.get("/api/admin/access/members")).json()
            assert members["members"][0]["display_name"] == "Kai"
            granted = await client.put("/api/admin/access/3")
            assert granted.status_code == 200
            assert any(item["discord_id"] == "3" for item in granted.json()["grants"])
            revoked = await client.delete("/api/admin/access/3")
            assert revoked.status_code == 200
            assert not any(
                item["discord_id"] == "3" for item in revoked.json()["grants"]
            )
            assert [item["action"] for item in revoked.json()["history"][:2]] == [
                "revoked",
                "granted",
            ]

    asyncio.run(scenario())


def test_listener_profiles_use_accounts_and_live_discord_details(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, _provider, _audio):
            response = await client.get(f"/api/profiles/{DISCORD_ID}")
            assert response.status_code == 200
            profile = response.json()
            assert profile["discord_id"] == DISCORD_ID
            assert profile["profile"]["name"] == "Listener"
            assert profile["profile"]["avatar"] == "0001"
            assert profile["role"] == "user"
            assert [member["guild_name"] for member in profile["members"]] == [
                "Fixture server",
                "Second server",
            ]

            services.access.operators = Operators(DISCORD_ID, ())
            promoted = await client.get(f"/api/profiles/{DISCORD_ID}")
            assert promoted.json()["role"] == "owner"

            assert (await client.get("/api/profiles/3")).status_code == 404
            client.cookies.clear()
            assert (await client.get(f"/api/profiles/{DISCORD_ID}")).status_code == 401

    asyncio.run(scenario())


def test_http_discovery_queue_receipts_undo_and_committed_events(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            stream = event_stream(services, TOKEN)
            try:
                assert (await anext(stream)).event == "state"
                first = await client.get("/api/catalog/search", params={"q": "Artist"})
                assert first.status_code == 200
                result = first.json()
                track_id, version = result["entries"][0]["track_id"], result["version"]
                assert (
                    await client.get("/api/catalog/search", params={"q": "Artist"})
                ).status_code == 200
                assert provider.calls == 1
                snapshot = await client.get(f"/api/catalog/search/{version}")
                assert snapshot.json()["entries"] == result["entries"]
                playlist = await client.post(
                    "/api/catalog/playlist",
                    json={
                        "source_url": "https://music.youtube.com/playlist?list=PLabcdefghijk"
                    },
                )
                assert playlist.status_code == 200
                assert [entry["track_id"] for entry in playlist.json()["entries"]] == [
                    track_id,
                    track_id,
                ]
                operation = str(uuid4())
                headers = {"Idempotency-Key": operation}
                added = await client.post(
                    "/api/queue",
                    json={"track_ids": [track_id, track_id]},
                    headers=headers,
                )
                assert added.status_code == 200
                data = added.json()
                assert data["outcome"]["added_count"] == 2
                assert len({entry["id"] for entry in data["state"]["queue"]}) == 2
                assert data["outcome"]["actor"]["name"] == "Listener"
                assert data["request_id"] == operation
                assert data["action"] == "queue.added"
                assert "session_id" not in data["state"]["queue"][0]
                assert "preparation" not in data["state"]["playback"]
                assert "provenance" not in data["state"]["tracks"][track_id]
                change = await anext(stream)
                assert change.event == "change" and change.id == "1"
                event = jsonable_encoder(change.data)
                assert event["request_id"] == operation
                assert event["action"] == data["action"]
                replay = await client.post(
                    "/api/queue",
                    json={"track_ids": [track_id, track_id]},
                    headers=headers,
                )
                assert (
                    replay.json()["replayed"]
                    and len(services.session.snapshot.queue.entries) == 2
                )
                assert (
                    await client.post(
                        "/api/queue", json={"track_ids": [track_id]}, headers=headers
                    )
                ).status_code == 409
                entry_id = data["state"]["queue"][0]["id"]
                removed = await client.delete(
                    f"/api/queue/{entry_id}", headers={"Idempotency-Key": str(uuid4())}
                )
                assert removed.json()["outcome"]["entries"][0]["track_id"] == track_id
                undo_id = removed.json()["outcome"]["undo_id"]
                undo = await client.post(
                    f"/api/queue/undo/{undo_id}",
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert undo.json()["outcome"]["restored_count"] == 1
                assert (
                    await client.post(
                        "/api/queue/clear",
                        json={"expected_queue_revision": 0},
                        headers={"Idempotency-Key": str(uuid4())},
                    )
                ).status_code == 409
                assert "stream.invalid" not in (await client.get("/api/session")).text
            finally:
                await stream.aclose()

    asyncio.run(scenario())


def test_removed_track_metadata_survives_reply_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            track = await services.catalog.track(provider.finding.reference.source_url)
            added = await client.post(
                "/api/queue",
                json={"track_ids": [str(track.id)]},
                headers={"Idempotency-Key": str(uuid4())},
            )
            entry = added.json()["outcome"]["entries"][0]
            headers = {"Idempotency-Key": str(uuid4())}
            for replayed in (False, True):
                removed = await client.delete(
                    f"/api/queue/{entry['id']}", headers=headers
                )
                data = removed.json()
                assert data["state"]["queue"] == []
                assert data["replayed"] is replayed
                assert (
                    data["state"]["tracks"][str(track.id)]["metadata"]["title"]
                    == "Амура"
                )
                assert data["action"] == "queue.removed"

    asyncio.run(scenario())


def test_playlist_refresh_preserves_version_title_and_pagination(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            body = {
                "source_url": "https://music.youtube.com/playlist?list=PLabcdefghijk"
            }
            original = (await client.post("/api/catalog/playlist", json=body)).json()
            version = UUID(original["version"])
            provider.playlist_title = "Renamed playlist"
            await client.post("/api/catalog/playlist", json=body | {"refresh": True})
            await until(
                lambda: (
                    services.catalog.refresh_status(
                        version, playlist=True
                    ).latest_version
                    != version
                )
            )
            updated = services.catalog.refresh_status(
                version, playlist=True
            ).latest_version
            for identifier, title in (
                (version, "Fixture"),
                (updated, "Renamed playlist"),
            ):
                page = (
                    await client.get(
                        f"/api/catalog/playlist/{identifier}", params={"limit": 1}
                    )
                ).json()
                assert page["playlist"]["title"] == title
                assert (
                    page["playlist"]["reference"] == original["playlist"]["reference"]
                )
                assert page["next_offset"] == 1
                assert page["source_has_more"] is False
                assert len(page["entries"]) == 1

    asyncio.run(scenario())


def test_schema_export_never_opens_runtime() -> None:
    def forbidden() -> AbstractAsyncContextManager[Services]:
        raise AssertionError("Schema generation tried to start services")

    schema = create_app(forbidden, public_origin="http://localhost").openapi()
    models = schema["components"]["schemas"]
    assert {
        "SessionView",
        "ChangeView",
        "MutationView",
        "DiscoveryView",
        "AccountView",
    } <= models.keys()
    assert "connection_id" not in models["PlaybackView"]["properties"]
    assert "queue.removed" in models["SessionAction"]["enum"]
    assert "request_id" in models["MutationView"]["required"]
    assert set(models["AccountView"]["required"]) == {
        "discord_id",
        "profile",
        "profile_complete",
        "is_admin",
        "role",
        "csrf_token",
        "expires_at",
        "appearance",
    }
    for path, method in [("/api/auth/session", "get"), ("/api/profile", "put")]:
        assert schema["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"].endswith("/AccountView")
    assert schema["paths"]["/api/events"]["get"]["x-sse-payloads"]["change"][
        "$ref"
    ].endswith("/ChangeView")


def test_schema_response_descriptions_do_not_depend_on_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> AbstractAsyncContextManager[Services]:
        raise AssertionError("Schema generation tried to start services")

    monkeypatch.setitem(http.client.responses, 422, "Unprocessable Entity")
    python312 = create_app(forbidden, public_origin="http://localhost").openapi()
    monkeypatch.setitem(http.client.responses, 422, "Unprocessable Content")
    python314 = create_app(forbidden, public_origin="http://localhost").openapi()

    assert python312 == python314
    assert (
        python312["paths"]["/api/queue"]["post"]["responses"]["422"]["description"]
        == "Unprocessable Content"
    )


def test_http_playback_guards_and_recovery_share_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, audio):
            track = (
                await client.post(
                    "/api/catalog/track",
                    json={"source_url": provider.finding.reference.source_url},
                )
            ).json()
            await client.post(
                "/api/queue",
                json={"track_ids": [track["id"]]},
                headers={"Idempotency-Key": str(uuid4())},
            )
            channels = (await client.get("/api/channels")).json()
            assert (
                channels[0]["guild_name"] == "Fixture server"
                and channels[0]["id"] == "123"
            )
            before = services.session.snapshot
            assert (
                await client.put(
                    "/api/connection",
                    json={"channel_id": str(2**63)},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 422
            assert services.session.snapshot == before
            assert (
                await client.put(
                    "/api/connection",
                    json={"channel_id": "123"},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 200
            await until(lambda: audio.progress is not None)
            audio.confirm(10)
            await until(lambda: len(services.session.snapshot.history) == 1)
            assert audio.progress
            attempt = str(audio.progress.attempt_id)
            paused = await client.post(
                "/api/playback/control",
                json={"action": "pause", "expected_attempt_id": attempt},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert paused.status_code == 200 and audio.progress.paused
            sought = await client.put(
                "/api/playback/position",
                json={"seconds": 25, "expected_attempt_id": attempt},
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert sought.status_code == 200
            await until(
                lambda: (
                    audio.progress is not None
                    and str(audio.progress.attempt_id) != attempt
                )
            )
            assert (
                await client.post(
                    "/api/playback/control",
                    json={"action": "skip", "expected_attempt_id": attempt},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 409
            assert len(services.session.snapshot.history) == 1
            assert (
                await client.put(
                    "/api/playback/crossfade",
                    json={"seconds": 5},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 200
            assert (
                await client.put(
                    "/api/playback/crossfade",
                    json={"seconds": 99},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 422

    asyncio.run(scenario())


def test_native_routes_require_session_and_mutations_require_csrf(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, _services, _provider, _audio):
            client.cookies.clear()
            for method, path in (
                ("GET", "/api/session"),
                ("GET", "/api/events"),
                ("GET", "/api/channels"),
                ("GET", "/api/catalog/search?q=music"),
                ("POST", "/api/catalog/playlist"),
                ("POST", "/api/catalog/track"),
                ("GET", f"/api/catalog/playlist/{uuid4()}"),
                ("POST", "/api/queue"),
                ("PUT", "/api/playback/volume"),
                ("PUT", "/api/connection"),
                ("POST", "/api/radio"),
                ("PUT", "/api/profile"),
            ):
                response = await client.request(method, path)
                assert response.status_code == 401, path
                assert response.headers["cache-control"] == "no-store"
            client.cookies.set(SESSION_COOKIE, TOKEN)
            for extra in (
                {"x-csrf-token": ""},
                {"origin": ""},
                {"origin": "https://wrong.invalid"},
            ):
                response = await client.post(
                    "/api/queue", json={"track_ids": [str(uuid4())]}, headers=extra
                )
                assert response.status_code == 403
            response = await client.post(
                "/api/queue",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "track_ids": [str(uuid4())],
                    "added_by": {
                        "id": str(uuid4()),
                        "name": "Forged",
                        "avatar": "0001",
                    },
                },
            )
            assert response.status_code == 422

    asyncio.run(scenario())


def test_authentication_profiles_csrf_and_live_revocation(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, _provider, _audio):
            assert (await client.get("/api/auth/session")).json()["profile"][
                "name"
            ] == "Listener"
            assert (
                await client.put(
                    "/api/profile", json={"name": "Renamed", "avatar": "0001"}
                )
            ).json()["profile"]["name"] == "Renamed"
            assert (
                await client.put(
                    "/api/profile/appearance", json={"primaryColor": "cyan"}
                )
            ).status_code == 200
            assert (await client.get("/api/auth/session")).json()["appearance"][
                "primaryColor"
            ] == "cyan"
            for headers in (
                {"origin": "http://wrong.invalid"},
                {"x-csrf-token": "wrong"},
            ):
                denied = await client.put(
                    "/api/profile",
                    json={"name": "Wrong", "avatar": "0001"},
                    headers=headers,
                )
                assert (
                    denied.status_code == 403
                    and denied.headers["cache-control"] == "no-store"
                )
            stream = event_stream(services, TOKEN)
            assert (await anext(stream)).event == "state"
            await services.access.revoke("9", DISCORD_ID)
            assert (await anext(stream)).event == "auth"
            await stream.aclose()
            assert (await client.get("/api/session")).status_code == 401
            client.cookies.clear()
            assert (await client.get("/api/session")).status_code == 401

    asyncio.run(scenario())


def test_failed_commit_does_not_publish_success_through_api(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            track = await services.catalog.track(provider.finding.reference.source_url)
            engine = database_engine(services.auth.settings.database_url)
            try:
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(
                        """
                        CREATE FUNCTION reject_receipts() RETURNS trigger AS $$
                        BEGIN
                            RAISE EXCEPTION 'fixture failure';
                        END;
                        $$ LANGUAGE plpgsql
                        """
                    )
                    await connection.exec_driver_sql(
                        """
                        CREATE TRIGGER reject_receipts
                        BEFORE INSERT ON operation_receipts
                        FOR EACH ROW EXECUTE FUNCTION reject_receipts()
                        """
                    )
                async with services.session.events.subscribe() as events:
                    response = await client.post(
                        "/api/queue",
                        json={"track_ids": [str(track.id)]},
                        headers={"Idempotency-Key": str(uuid4())},
                    )
                    assert response.status_code == 503
                    assert (
                        events.empty() and services.session.snapshot.queue.entries == ()
                    )
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_old_discovery_selection_survives_refresh_and_cache_close(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            old = (
                await client.get("/api/catalog/search", params={"q": "Artist"})
            ).json()
            original = old["entries"][0]["track_id"]
            provider.finding = replace(
                provider.finding,
                reference=MediaReference(
                    MediaIdentity("youtube", "ZYXWVUTSRQP"),
                    MediaKind.TRACK,
                    "https://youtu.be/ZYXWVUTSRQP",
                ),
            )
            refresh = (
                await client.get(
                    "/api/catalog/search", params={"q": "Artist", "refresh": "true"}
                )
            ).json()
            assert refresh["version"] == old["version"]
            await until(
                lambda: (
                    not services.catalog.refresh_status(UUID(old["version"])).refreshing
                )
            )
            latest = services.catalog.refresh_status(
                UUID(old["version"])
            ).latest_version
            assert str(latest) != old["version"]
            assert (await client.get(f"/api/catalog/search/{old['version']}")).json()[
                "entries"
            ][0]["track_id"] == original
            assert (await client.get(f"/api/catalog/search/{latest}")).json()[
                "entries"
            ][0]["track_id"] != original
            await services.catalog.close()
            operation = {"Idempotency-Key": str(uuid4())}
            queued = await client.post(
                "/api/queue",
                json={"track_ids": [original, original]},
                headers=operation,
            )
            assert (
                queued.status_code == 200
                and queued.json()["outcome"]["added_count"] == 2
            )
            replay = await client.post(
                "/api/queue",
                json={"track_ids": [original, original]},
                headers=operation,
            )
            assert replay.json()["replayed"]

    asyncio.run(scenario())


def test_radio_and_move_routes_use_engine_conflict_checks(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, services, provider, _audio):
            started = await client.post(
                "/api/radio",
                json={
                    "seed": jsonable_encoder(provider.finding.reference),
                    "expected_generation": None,
                },
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert started.status_code == 200
            generation = started.json()["state"]["radio"]["generation"]
            await until(lambda: len(services.session.snapshot.queue.entries) == 1)
            state = (await client.get("/api/session")).json()
            entry = state["queue"][0]
            assert (
                entry["origin"] == "radio" and entry["added_by"]["name"] == "Listener"
            )
            moved = await client.put(
                f"/api/queue/{entry['id']}/position",
                json={
                    "before_entry_id": None,
                    "expected_queue_revision": state["session"]["queue_revision"],
                },
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert moved.status_code == 200
            stopped = await client.post(
                f"/api/radio/{generation}/stop",
                headers={"Idempotency-Key": str(uuid4())},
            )
            assert (
                stopped.status_code == 200
                and stopped.json()["state"]["radio"]["mode"] == "manual"
            )
            assert (
                await client.post(
                    f"/api/radio/{generation}/retry",
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 409

    asyncio.run(scenario())


def test_oauth_cookie_lifecycle_and_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (client, _services, _provider, _audio):
            client.cookies.clear()
            login = await client.get("/api/auth/discord")
            assert (
                login.status_code == 303 and "HttpOnly" in login.headers["set-cookie"]
            )
            state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
            callback = await client.get(
                "/api/auth/discord/callback",
                params={"state": state, "code": "fixture-code"},
            )
            assert callback.status_code == 303 and callback.headers["location"] == "/"
            assert "HttpOnly" in callback.headers["set-cookie"]
            replay = await client.get(
                "/api/auth/discord/callback",
                params={"state": state, "code": "fixture-code"},
            )
            assert "login_expired" in replay.headers["location"]
            signed_in = (await client.get("/api/auth/session")).json()
            logout = await client.post(
                "/api/auth/logout", headers={"x-csrf-token": signed_in["csrf_token"]}
            )
            assert logout.status_code == 204
            assert (await client.get("/api/session")).status_code == 401

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["shutdown", "logout", "expire", "revoke"])
def test_real_sse_response_resynchronizes_and_closes_cleanly(
    tmp_path: Path, ending: str
) -> None:
    async def scenario() -> None:
        async with fixture(tmp_path) as (_client, services, _provider, _audio):

            @asynccontextmanager
            async def borrow() -> AsyncGenerator[Services]:
                yield services

            shutdown = asyncio.Event()
            app = create_app(
                borrow, public_origin="http://localhost", shutdown_event=shutdown
            )

            async def transport(scope: Scope, receive: Receive, send: Send) -> None:
                async def observed_send(message: Message) -> None:
                    await send(message)
                    if message[
                        "type"
                    ] == "http.response.body" and b"event: state" in message.get(
                        "body", b""
                    ):
                        if ending == "shutdown":
                            shutdown.set()
                        elif ending == "logout":
                            assert (
                                await _client.post("/api/auth/logout")
                            ).status_code == 204
                        elif ending == "revoke":
                            await services.access.revoke("9", DISCORD_ID)
                        else:
                            engine = account_engine(services.auth.settings.database_url)
                            try:
                                with DatabaseSession(engine) as db, db.begin():
                                    db.execute(update(SessionRow).values(expires_at=0))
                            finally:
                                engine.dispose()

                await app(scope, receive, observed_send)

            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=transport),
                    base_url="http://localhost",
                    cookies={SESSION_COOKIE: TOKEN},
                ) as client,
            ):
                async with asyncio.timeout(5):
                    response = await client.get(
                        "/api/events", headers={"Last-Event-ID": "outdated"}
                    )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-store"
                assert "event: state" in response.text
                data = next(
                    line.removeprefix("data: ")
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                )
                assert json.loads(data)["queue"] == []
                if ending != "shutdown":
                    assert "event: auth" in response.text
                    assert (await client.get("/api/session")).status_code in (401, 403)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["schema", "start"])
def test_partial_startup_closes_all_resources_and_session_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def scenario() -> None:
        path = tmp_path / "startup.db"
        engine = database_engine(database_url(path))
        try:
            async with engine.begin() as connection:
                if failure == "start":
                    await connection.run_sync(upgrade)
        finally:
            await engine.dispose()
        auth = Auth(
            AuthSettings(
                "http://localhost",
                "",
                "",
                database_url(path),
                tmp_path / "access.toml",
            ),
            ("0001",),
            access=Access(database_url(path), Operators("9", ())),
            provider=Identity(),
        )
        provider, audio, voice = Provider(), ControlledOutput(), Voice()
        closed: list[str] = []

        async def close_audio() -> None:
            closed.append("audio")

        async def close_voice() -> None:
            closed.append("voice")

        def fail_volume(value: float) -> None:
            raise RuntimeError("fixture startup failure")

        monkeypatch.setattr(audio, "close", close_audio)
        monkeypatch.setattr(voice, "close", close_voice)
        if failure == "start":
            monkeypatch.setattr(audio, "set_volume", fail_volume)
        with pytest.raises((ValueError, RuntimeError)):
            async with open_engine(
                uuid4(),
                auth=auth,
                providers=(provider,),
                audio=audio,
                voice=voice,
                clock=lambda: TIME,
            ):
                pytest.fail("Broken startup must not expose services")
        assert closed == ["audio", "voice"] and provider.closed
        assert not any(
            task.get_name().startswith("engine-") for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())

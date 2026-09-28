# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
import os
from pathlib import Path
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
import httpx
from sqlalchemy import insert

from nahoermaar.api.app import create_app
from nahoermaar.api.events import AuthEventView, ChangeView, event_stream
from nahoermaar.api.player import PlayerView
from nahoermaar.catalog.maintenance import CatalogMaintenance
from nahoermaar.catalog.service import CatalogService
from nahoermaar.bootstrap import (  # pyright: ignore[reportPrivateUsage]
    Application,
    _register_handlers,  # pyright: ignore[reportPrivateUsage]
)
from nahoermaar.config import AuthSettings, Settings
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.integrations.discord import DiscordGateway
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.messaging import MessageBus
from nahoermaar.listening.service import ListeningService
from nahoermaar.observability import ContextFilter
from nahoermaar.operations.incidents import IncidentService
from nahoermaar.operations.jobs import JobId, JobRunDetail, JobRunService, JobTrigger
from nahoermaar.operations.logs import RecentLogBuffer
from nahoermaar.operations.maintenance import (
    HousekeepingContext,
    HousekeepingContribution,
    HousekeepingMaintenance,
    cleanup_detail,
)
from nahoermaar.operations.scheduler import JobCoordinator
from nahoermaar.player.automation import PlaybackAutomation
from nahoermaar.player.events import PlaybackRuntimeChanged
from nahoermaar.player.session import CatalogRadioResolver, PlayerSessionManager
from nahoermaar.statistics.service import StatisticsService
from nahoermaar.users.service import (
    AccessService,
    AuthService,
    Operators,
    ProvidedDiscordIdentity,
    SESSION_COOKIE,
)
from nahoermaar.users.domain import DiscordMember
from nahoermaar.views.profile import ProfileView
from nahoermaar.views.history import PlaybackHistoryView

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ROOT = Path(__file__).parents[3]
ORIGIN = "http://localhost:3000"


class Provider:
    state = ""
    verifier = ""
    redirect_uri = ""

    async def authorization_url(
        self, state: str, verifier: str, redirect_uri: str
    ) -> str:
        self.state = state
        self.verifier = verifier
        self.redirect_uri = redirect_uri
        return "https://discord.example/authorize"

    async def identity(
        self, code: str, verifier: str, redirect_uri: str
    ) -> ProvidedDiscordIdentity:
        assert code == "oauth-code"
        assert verifier == self.verifier
        assert redirect_uri == self.redirect_uri
        return ProvidedDiscordIdentity("9", "Owner", None)


class Gateway:
    def members(self) -> tuple[DiscordMember, ...]:
        return (
            DiscordMember(
                "9",
                "owner",
                "Andrey",
                "https://cdn.discordapp.com/avatars/9/test.png",
                "1",
                "Spoon's server",
            ),
        )


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_auth_profile_access_origin_and_csrf_share_one_api_boundary() -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    provider = Provider()
    access = AccessService(units, Operators("9", ()), clock=lambda: NOW)
    auth = AuthService(units, provider, clock=lambda: NOW)
    incidents = IncidentService(units, clock=lambda: NOW)
    job_runs = JobRunService(units, incidents, clock=lambda: NOW)
    bus = MessageBus(incidents)
    catalog = CatalogService(units, ())
    catalog_maintenance = CatalogMaintenance(
        units,
        catalog,
        clock=lambda: NOW,
        delay=0,
    )

    async def no_housekeeping_changes(
        _context: HousekeepingContext,
    ) -> tuple[JobRunDetail, ...]:
        return (cleanup_detail("operations", "Expired data", 0),)

    housekeeping = HousekeepingMaintenance(
        (
            HousekeepingContribution(
                "operations",
                ("Expired data",),
                no_housekeeping_changes,
            ),
        )
    )
    jobs = JobCoordinator(
        job_runs,
        (
            replace(catalog_maintenance.definition(), run_on_startup=False),
            replace(housekeeping.definition(), run_on_startup=False),
        ),
        clock=lambda: NOW,
    )
    player = PlayerSessionManager(units, bus, CatalogRadioResolver(catalog))
    listening = ListeningService(
        units,
        bus,
        access,
    )
    avatars = DiscordAvatarStore(Path("data/avatars"))
    automation = PlaybackAutomation(
        player,
        bus,
        empty_channel_grace_seconds=60,
    )
    statistics = StatisticsService(
        units,
        ZoneInfo("UTC"),
        access,
        clock=lambda: NOW,
    )
    profiles = ProfileView(units, statistics)
    history = PlaybackHistoryView(units)
    logs = RecentLogBuffer()
    logs.addFilter(ContextFilter())
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logs)
    _register_handlers(bus, auth, access, player, listening, automation)
    settings = Settings(
        os.environ["DATABASE_URL"],
        AuthSettings(
            ORIGIN,
            "123",
            "secret",
            Path("access.toml"),
            frozenset({"http://localhost:3001"}),
        ),
    )
    application = Application(
        settings,
        database,
        bus,
        auth,
        access,
        catalog,
        player,
        listening,
        statistics,
        profiles,
        history,
        incidents,
        automation,
        job_runs,
        jobs,
        logs,
        avatars,
        gateway=cast(DiscordGateway, Gateway()),
    )
    app = create_app(application)

    @app.get("/api/_test/broken", include_in_schema=False)
    async def broken() -> None:
        raise RuntimeError("test failure")

    contract = app.openapi()
    assert "/api/events" in contract["paths"]
    assert "/api/listening/recent" in contract["paths"]
    assert "/api/logs" in contract["paths"]
    assert "/api/jobs" in contract["paths"]
    assert "/api/jobs/runs" in contract["paths"]
    assert "/api/jobs/runs/{run_id}" in contract["paths"]
    assert "/api/jobs/{job_id}/runs" in contract["paths"]
    assert "/api/jobs/housekeeping" not in contract["paths"]
    assert "/api/incidents" in contract["paths"]
    assert "/api/player/sleep-timer" in contract["paths"]
    assert "/api/statistics/overview" in contract["paths"]
    assert "/api/statistics/users/{user_id}" in contract["paths"]
    assert "ErrorView" in contract["components"]["schemas"]
    assert contract["paths"]["/api/player"]["get"]["responses"]["401"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ErrorView"}
    assert contract["paths"]["/api/events"]["get"]["x-sse-payloads"] == {
        "state": {"$ref": "#/components/schemas/PlayerView"},
        "change": {"$ref": "#/components/schemas/ChangeView"},
        "auth": {"$ref": "#/components/schemas/AuthEventView"},
    }

    async def scenario() -> None:
        await access.reconcile()
        await player.start()
        await jobs.start()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=ORIGIN,
            follow_redirects=False,
        ) as client:
            signed_out_catalog = await client.get(
                "/api/catalog/search", params={"q": "test"}
            )
            assert signed_out_catalog.status_code == 401
            assert (await client.get("/api/player")).status_code == 401

            begin = await client.get("/api/auth/discord")
            assert begin.status_code == 302
            assert begin.headers["location"] == "https://discord.example/authorize"
            assert provider.redirect_uri == ORIGIN + "/api/auth/discord/callback"

            callback = await client.get(
                "/api/auth/discord/callback",
                params={"state": provider.state, "code": "oauth-code"},
            )
            assert callback.status_code == 302
            assert callback.headers["location"] == ORIGIN + "/profile"
            session_token = client.cookies.get(SESSION_COOKIE)
            assert session_token is not None
            current = await auth.authenticate(session_token)

            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            assert session.json()["role"] == "owner"

            overview = await client.get("/api/statistics/overview")
            assert overview.status_code == 200
            assert overview.json()["totals"]["playback"]["overall"]["started"] == 0
            assert overview.json()["totals"]["playback_seconds"] == 0.0
            assert overview.json()["active_listeners"] == 0
            assert overview.json()["requested_tracks"] == []
            assert overview.json()["requested_artists"] == []
            assert overview.json()["highlights"] == {
                "most_shared_track": None,
                "listener_pair": None,
                "radio_conversion": None,
                "contagious_track": None,
                "busiest_weekday": None,
                "busiest_hour": None,
                "active_day_streaks": {"current": 0, "longest": 0},
                "average_listeners": None,
            }
            assert "user_id" not in overview.json()
            own_statistics = await client.get(
                f"/api/statistics/users/{current.user.id}"
            )
            assert own_statistics.status_code == 200
            assert own_statistics.json()["user_id"] == str(current.user.id)
            assert "active_listeners" not in own_statistics.json()
            assert "top_listeners" not in own_statistics.json()
            assert "requested_tracks" not in own_statistics.json()
            assert "requested_artists" not in own_statistics.json()
            assert own_statistics.json()["top_tracks_by_listening"] == []
            assert own_statistics.json()["top_artists_by_listening"] == []
            personal_highlights = own_statistics.json()["highlights"]
            assert personal_highlights["group_listening_share"] is None
            assert personal_highlights["request_outcomes"] == {
                "manual_requests": 0,
                "played_requests": 0,
                "completed_requests": 0,
                "play_rate": None,
                "completion_rate": None,
            }
            assert personal_highlights["radio_discoveries"] == []
            assert personal_highlights["influenced_tracks"] == []
            assert personal_highlights["badges"] == []
            assert len(personal_highlights["listening_pattern"]["weekdays"]) == 7
            assert len(personal_highlights["listening_pattern"]["hours"]) == 24
            own_profile = await client.get(f"/api/profiles/{current.user.id}")
            assert own_profile.status_code == 200
            assert own_profile.json()["id"] == str(current.user.id)
            assert own_profile.json()["discord"]["display_name"] == "Andrey"
            assert own_profile.json()["discord"]["avatar_url"] == (
                application.avatars.public_url(
                    "9",
                    source_url="https://cdn.discordapp.com/avatars/9/test.png",
                )
            )
            assert own_profile.json()["statistics"]["user_id"] == str(current.user.id)
            assert own_profile.json()["recent_tracks"] == []
            current_profile = await client.get("/api/profiles/me")
            assert current_profile.status_code == 200
            assert current_profile.json() == own_profile.json()
            own_account = await client.get("/api/users/me")
            assert own_account.status_code == 200
            assert own_account.json() == {
                key: own_profile.json()[key]
                for key in (
                    "id",
                    "discord",
                    "profile",
                    "appearance",
                    "role",
                    "created_at",
                    "updated_at",
                    "last_login_at",
                )
            }

            player_state = await client.get("/api/player")
            assert player_state.status_code == 200
            assert player_state.json()["queue"] == []
            assert player_state.json()["crossfade_seconds"] == 7
            assert player_state.json()["sleep_timer_expires_at"] is None
            assert player_state.json()["runtime"] == {
                "phase": "disabled",
                "current": None,
                "playback_id": None,
                "attempt_id": None,
                "position_seconds": 0.0,
                "position_updated_at": None,
                "duration_seconds": None,
                "voice": {
                    "phase": "disconnected",
                    "channel_id": None,
                    "attempt": 0,
                    "error_code": None,
                },
                "last_error_code": None,
            }
            assert (await client.get("/api/listening/recent")).json() == {
                "entries": [],
                "contributors": [],
                "page": 1,
                "page_size": 20,
                "total": 0,
                "page_count": 0,
                "snapshot": None,
            }
            invalid_recent = await client.get(
                "/api/listening/recent", params={"page_size": 0}
            )
            assert invalid_recent.status_code == 422
            assert invalid_recent.json() == {"error": "validation_failed"}
            events = event_stream(application, session_token)
            initial = await anext(events)
            assert initial.event == "state"
            assert initial.id == "0"
            assert isinstance(initial.data, PlayerView)
            assert initial.data.queue == ()

            rejected = await client.put(
                "/api/users/me/profile",
                json={"display_name": "Owner"},
            )
            assert rejected.status_code == 403
            assert rejected.json() == {"error": "csrf_failed"}

            incident_report = await client.get("/api/incidents")
            assert incident_report.status_code == 200
            assert incident_report.json()["retention_days"] == 14
            assert incident_report.json()["totals"]["rejected"] >= 1
            assert incident_report.json()["associated_users"][0]["user_id"] == str(
                current.user.id
            )

            headers = {
                "origin": ORIGIN,
                "x-csrf-token": current.csrf,
            }
            jobs_response = await client.get("/api/jobs")
            assert jobs_response.status_code == 200
            assert [item["id"] for item in jobs_response.json()["jobs"]] == [
                "catalog-maintenance",
                "housekeeping",
            ]
            assert jobs_response.json()["jobs"][0]["default_batch_size"] == 10
            assert [item["health"] for item in jobs_response.json()["jobs"]] == [
                "unknown",
                "unknown",
            ]
            assert "recent_runs" not in jobs_response.json()
            assert jobs_response.json()["history_retention_days"] == 30
            job_runs = await client.get("/api/jobs/runs")
            assert job_runs.status_code == 200
            assert job_runs.json() == {"entries": [], "next_cursor": None}
            missing_job_run = await client.get(f"/api/jobs/runs/{uuid4()}")
            assert missing_job_run.status_code == 404
            assert missing_job_run.json() == {
                "error": "not_found",
                "retryable": False,
            }
            invalid_job_cursor = await client.get(
                "/api/jobs/runs",
                params={"cursor": "not-a-cursor"},
            )
            assert invalid_job_cursor.status_code == 422
            assert invalid_job_cursor.json() == {
                "error": "validation_failed",
                "retryable": False,
            }
            first_run = await application.job_runs.start_run(
                JobId.HOUSEKEEPING,
                JobTrigger.MANUAL,
                1,
                current.user.id,
            )
            first_run = await application.job_runs.finish_run(
                first_run,
                candidate_count=1,
                processed_count=1,
                changed_count=1,
            )
            second_run = await application.job_runs.start_run(
                JobId.HOUSEKEEPING,
                JobTrigger.MANUAL,
                1,
                current.user.id,
            )
            second_run = await application.job_runs.finish_run(
                second_run,
                candidate_count=1,
                processed_count=1,
                changed_count=0,
            )
            first_run_page = await client.get(
                "/api/jobs/runs",
                params={"limit": 1, "job_id": "housekeeping"},
            )
            assert first_run_page.status_code == 200
            assert len(first_run_page.json()["entries"]) == 1
            assert "details" not in first_run_page.json()["entries"][0]
            assert first_run_page.json()["next_cursor"] is not None
            second_run_page = await client.get(
                "/api/jobs/runs",
                params={
                    "limit": 1,
                    "job_id": "housekeeping",
                    "cursor": first_run_page.json()["next_cursor"],
                },
            )
            assert second_run_page.status_code == 200
            paged_ids = {
                first_run_page.json()["entries"][0]["id"],
                second_run_page.json()["entries"][0]["id"],
            }
            assert paged_ids == {str(first_run.id), str(second_run.id)}
            assert second_run_page.json()["next_cursor"] is None
            run_detail = await client.get(f"/api/jobs/runs/{first_run.id}")
            assert run_detail.status_code == 200
            assert run_detail.json()["id"] == str(first_run.id)
            assert run_detail.json()["details"] == []
            started_job = await client.post(
                "/api/jobs/catalog-maintenance/runs",
                headers=headers,
                json={"batch_size": 3},
            )
            assert started_job.status_code == 202
            assert started_job.json()["running"] is True
            assert started_job.json()["active_batch_size"] == 3
            track_id = uuid4()
            async with units() as work:
                await work.session.execute(
                    insert(Base.metadata.tables["tracks"]).values(
                        id=track_id,
                        title="API track",
                        duration_seconds=180.0,
                        artwork_url=None,
                        album_title=None,
                        release_date=None,
                        isrc=None,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await work.commit()
            operation_id = uuid4()
            queued = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": str(operation_id),
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert queued.status_code == 200
            assert queued.json()["player"]["queue"][0]["request"][
                "requested_by"
            ] == str(current.user.id)
            contributor = queued.json()["player"]["queue"][0]["request"]["contributor"]
            assert contributor == {
                "user_id": str(current.user.id),
                "display_name": "Owner",
                "discord_id": "9",
                "discord_username": "Owner",
                "avatar_url": application.avatars.public_url("9"),
            }
            request_id = queued.json()["player"]["queue"][0]["request"]["id"]
            playback_id = uuid4()
            prior_playback_id = uuid4()
            async with units() as work:
                await work.session.execute(
                    insert(Base.metadata.tables["playback_records"]).values(
                        id=playback_id,
                        session_id=application.player.state.session.id,
                        request_id=request_id,
                        started_at=NOW,
                        audio_seconds=42.0,
                        group_audio_seconds=40.0,
                        ended_at=NOW + timedelta(seconds=42),
                        end_reason="completed",
                    )
                )
                await work.session.execute(
                    insert(Base.metadata.tables["playback_records"]).values(
                        id=prior_playback_id,
                        session_id=application.player.state.session.id,
                        request_id=request_id,
                        started_at=NOW - timedelta(seconds=60),
                        audio_seconds=50.0,
                        group_audio_seconds=48.0,
                        ended_at=NOW - timedelta(seconds=10),
                        end_reason="completed",
                    )
                )
                await work.session.execute(
                    insert(Base.metadata.tables["playback_listeners"]).values(
                        playback_id=prior_playback_id,
                        user_id=current.user.id,
                        audio_seconds=40.0,
                        first_heard_at=NOW - timedelta(seconds=60),
                        last_heard_at=NOW - timedelta(seconds=20),
                    )
                )
                await work.commit()
            enriched_overview = await client.get("/api/statistics/overview")
            assert enriched_overview.status_code == 200
            top_listener = enriched_overview.json()["top_listeners"][0]
            assert top_listener["discord_username"] == "Owner"
            assert top_listener["discord_display_name"] == "Andrey"
            assert top_listener["avatar_url"] == application.avatars.public_url(
                "9",
                source_url="https://cdn.discordapp.com/avatars/9/test.png",
            )
            recent = await client.get("/api/listening/recent", params={"page_size": 1})
            assert recent.status_code == 200
            recent_payload = recent.json()
            snapshot = recent_payload.pop("snapshot")
            assert snapshot
            assert recent_payload == {
                "entries": [
                    {
                        "playback_id": str(playback_id),
                        "request_id": request_id,
                        "track_id": str(track_id),
                        "title": "API track",
                        "artist_names": [],
                        "artwork_url": None,
                        "duration_seconds": 180.0,
                        "origin": "manual",
                        "requested_by": str(current.user.id),
                        "radio_run_id": None,
                        "source_id": None,
                        "source_url": None,
                        "source_provider": None,
                        "contributor": {
                            "user_id": str(current.user.id),
                            "display_name": "Owner",
                            "discord_id": "9",
                            "discord_username": "Owner",
                            "avatar_url": application.avatars.public_url("9"),
                        },
                        "started_at": NOW.isoformat().replace("+00:00", "Z"),
                        "ended_at": (NOW + timedelta(seconds=42))
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "end_reason": "completed",
                        "audio_seconds": 42.0,
                        "group_audio_seconds": 40.0,
                    }
                ],
                "contributors": [
                    {
                        "user_id": str(current.user.id),
                        "display_name": "Owner",
                        "avatar_url": application.avatars.public_url("9"),
                    }
                ],
                "page": 1,
                "page_size": 1,
                "total": 2,
                "page_count": 2,
            }
            older = await client.get(
                "/api/listening/recent",
                params={"page": 2, "page_size": 1, "snapshot": snapshot},
            )
            assert older.status_code == 200
            assert older.json()["entries"][0]["playback_id"] == str(prior_playback_id)
            assert older.json()["snapshot"] == snapshot
            search = await client.get(
                "/api/listening/recent", params={"q": "api TRACK"}
            )
            assert search.status_code == 200
            assert search.json()["total"] == 2
            manual = await client.get(
                "/api/listening/recent",
                params={"radio": False, "requested_by": str(current.user.id)},
            )
            assert manual.status_code == 200
            assert manual.json()["total"] == 2
            assert all(
                entry["origin"] == "manual" for entry in manual.json()["entries"]
            )
            change = await anext(events)
            assert change.event == "change"
            assert change.id == "1"
            assert isinstance(change.data, ChangeView)
            assert change.data.operation_id == operation_id
            assert change.data.causation_id is not None
            assert change.data.causation_id != change.data.message_id
            assert change.data.state.queue[0].request.requested_by == current.user.id
            assert change.data.state.queue[0].request.contributor is not None
            assert (
                change.data.state.queue[0].request.contributor.display_name == "Owner"
            )
            await bus.publish(
                PlaybackRuntimeChanged(application.player.state.session.id)
            )
            runtime = await anext(events)
            assert runtime.event == "state"
            assert runtime.id == "1"
            assert isinstance(runtime.data, PlayerView)
            assert runtime.data.revision == 1
            replayed = await client.post(
                "/api/player/queue",
                headers=headers,
                json={
                    "operation_id": str(operation_id),
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["replayed"] is True
            assert len(replayed.json()["player"]["queue"]) == 1

            cleared = await client.post(
                "/api/player/queue/clear",
                headers=headers,
                json={
                    "operation_id": str(uuid4()),
                    "expected_queue_revision": replayed.json()["player"][
                        "queue_revision"
                    ],
                    "requested_by": str(current.user.id),
                },
            )
            assert cleared.status_code == 200
            assert cleared.json()["player"]["queue"] == []
            assert cleared.json()["outcome"]["removed_count"] == 1
            await anext(events)
            restored = await client.post(
                "/api/player/queue/undo",
                headers=headers,
                json={
                    "operation_id": str(uuid4()),
                    "undo_id": cleared.json()["outcome"]["undo_id"],
                },
            )
            assert restored.status_code == 200
            assert len(restored.json()["player"]["queue"]) == 1
            await anext(events)

            profile = await client.put(
                "/api/users/me/profile",
                headers=headers,
                json={"display_name": "Local owner"},
            )
            assert profile.status_code == 200
            assert profile.json()["profile"]["display_name"] == "Local owner"
            assert profile.json()["discord"]["username"] == "Owner"
            assert "statistics" not in profile.json()

            played = await client.post(
                "/api/player/play",
                headers=headers,
                json={"operation_id": str(uuid4())},
            )
            assert played.status_code == 200
            runtime = played.json()["player"]["runtime"]
            assert runtime["phase"] == "starting"
            assert runtime["current"]["id"] == request_id
            assert runtime["current"]["track"]["title"] == "API track"
            assert runtime["current"]["contributor"]["display_name"] == "Local owner"
            assert runtime["current"]["contributor"]["avatar_url"] == (
                application.avatars.public_url("9")
            )

            operator = await client.put("/api/access/9", headers=headers)
            assert operator.status_code == 409
            assert operator.json() == {"error": "operator_access_managed_in_config"}

            grant = await client.put("/api/access/7", headers=headers)
            assert grant.status_code == 200
            assert grant.json()["role_after"] == "user"
            access_state = await client.get("/api/access")
            assert access_state.status_code == 200
            assert access_state.json()["operators"][0]["id"] == str(current.user.id)
            assert access_state.json()["operators"][0]["role"] == "owner"
            assert access_state.json()["grants"][0]["user"]["discord"]["id"] == "7"
            members = await client.get("/api/access/members")
            assert members.status_code == 200
            assert members.json()["members"][0]["display_name"] == "Andrey"
            granted_user_id = access_state.json()["grants"][0]["user"]["id"]
            granted_profile = await client.get(f"/api/profiles/{granted_user_id}")
            assert granted_profile.status_code == 200

            revoked = await client.delete("/api/access/7", headers=headers)
            assert revoked.status_code == 200
            hidden_profile = await client.get(f"/api/profiles/{granted_user_id}")
            assert hidden_profile.status_code == 404
            assert hidden_profile.json() == {"error": "profile_not_found"}

            process_logs = await client.get("/api/logs", params={"limit": 200})
            assert process_logs.status_code == 200
            entries = process_logs.json()["entries"]
            assert entries
            assert any(entry["actor_id"] == str(current.user.id) for entry in entries)
            assert all("q=test" not in entry["message"] for entry in entries)
            assert all("state=" not in entry["message"] for entry in entries)

            wrong_origin = await client.get(
                "/api/users/me", headers={"origin": "https://evil.example"}
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json() == {"error": "origin_forbidden"}

            development_origin = await client.put(
                "/api/users/me/profile",
                headers={
                    "origin": "http://localhost:3001",
                    "x-csrf-token": current.csrf,
                },
                json={"display_name": "Local owner"},
            )
            assert development_origin.status_code == 200

            development_login = await client.get(
                "/api/auth/discord",
                headers={"x-nahormaar-browser-origin": "http://localhost:3001"},
            )
            assert development_login.status_code == 302
            assert provider.redirect_uri == (
                "http://localhost:3001/api/auth/discord/callback"
            )

            rejected_login_origin = await client.get(
                "/api/auth/discord",
                headers={"x-nahormaar-browser-origin": "https://evil.example"},
            )
            assert rejected_login_origin.status_code == 403
            assert rejected_login_origin.json() == {"error": "origin_forbidden"}

            missing = await client.get("/api/_test/missing")
            assert missing.status_code == 404
            assert missing.json() == {"error": "not_found", "retryable": False}

            broken = await client.get("/api/_test/broken")
            assert broken.status_code == 500
            assert broken.json() == {"error": "internal_error", "retryable": True}

            incident_report = await client.get("/api/incidents")
            http_incidents = [
                item
                for item in incident_report.json()["recent"]
                if item["component"] == "http"
            ]
            http_codes = {item["error_code"] for item in http_incidents}
            assert {
                "csrf_failed",
                "internal_error",
                "origin_forbidden",
            } <= http_codes
            assert "validation_failed" not in http_codes
            assert "not_found" not in http_codes
            assert "profile_not_found" not in http_codes
            assert "operator_access_managed_in_config" not in http_codes
            assert (
                sum(item["error_code"] == "internal_error" for item in http_incidents)
                == 1
            )

            sleep_timer = await client.put(
                "/api/player/sleep-timer",
                headers=headers,
                json={"operation_id": str(uuid4()), "seconds": 900},
            )
            assert sleep_timer.status_code == 200
            assert sleep_timer.json()["outcome"]["action"] == "sleep_timer.set"
            assert sleep_timer.json()["player"]["sleep_timer_expires_at"] is not None
            await anext(events)
            cancelled_sleep_timer = await client.request(
                "DELETE",
                "/api/player/sleep-timer",
                headers=headers,
                json={"operation_id": str(uuid4())},
            )
            assert cancelled_sleep_timer.status_code == 200
            assert (
                cancelled_sleep_timer.json()["outcome"]["action"]
                == "sleep_timer.cancelled"
            )
            assert (
                cancelled_sleep_timer.json()["player"]["sleep_timer_expires_at"] is None
            )
            await anext(events)

            waiting = asyncio.create_task(anext(events))
            await asyncio.sleep(0)
            await auth.logout(session_token)
            player.events.reauthenticate()
            auth_event = await asyncio.wait_for(waiting, timeout=1)
            assert auth_event.event == "auth"
            assert isinstance(auth_event.data, AuthEventView)
            assert auth_event.data.error == "signed_out"
            await events.aclose()
        await jobs.close()
        await player.close()
        await catalog.close()

    try:
        asyncio.run(scenario())
    finally:
        root_logger.removeHandler(logs)
        root_logger.setLevel(previous_level)
        asyncio.run(database.close())

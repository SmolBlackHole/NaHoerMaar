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

from alembic import command
from alembic.config import Config
import httpx
from sqlalchemy import insert

from nahoermaar.api.app import create_app
from nahoermaar.api.events import AuthEventView, ChangeView, event_stream
from nahoermaar.api.player import PlayerView
from nahoermaar.catalog.maintenance import CatalogMaintenance
from nahoermaar.catalog.main import CatalogModule
from nahoermaar.catalog.service import CatalogService
from nahoermaar.bootstrap import Application
from nahoermaar.config import AuthSettings, Settings
from nahoermaar.database.core import Database
from nahoermaar.database.schema import Base
from nahoermaar.database.uow import UnitOfWork
from nahoermaar.integrations.discord import DiscordGateway
from nahoermaar.integrations.avatars import DiscordAvatarStore
from nahoermaar.integrations.main import IntegrationsModule
from nahoermaar.lifecycle import LifecycleResource
from nahoermaar.messaging import MessageBus
from nahoermaar.listening.main import create_listening_module
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
from nahoermaar.operations.main import OperationsModule
from nahoermaar.operations.scheduler import JobCoordinator
from nahoermaar.player.events import PlaybackRuntimeChanged
from nahoermaar.player.main import complete_player_module, prepare_player_module
from nahoermaar.statistics.main import create_statistics_module
from nahoermaar.users.main import create_users_module
from nahoermaar.users.service import (
    ProvidedDiscordIdentity,
    SESSION_COOKIE,
)
from nahoermaar.users.domain import DiscordMember
from nahoermaar.views.main import create_views_module

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
    def members(
        self,
        *,
        query: str | None = None,
        guild_id: str | None = None,
    ) -> tuple[DiscordMember, ...]:
        members = (
            DiscordMember(
                "9",
                "owner",
                "Andrey",
                "https://cdn.discordapp.com/avatars/9/test.png",
                "1",
                "Spoon's server",
            ),
        )
        needle = query.casefold() if query else ""
        return tuple(
            member
            for member in members
            if (guild_id is None or member.guild_id == guild_id)
            and (
                not needle
                or needle in member.username.casefold()
                or needle in member.display_name.casefold()
                or needle in member.discord_id
                or needle in member.guild_name.casefold()
            )
        )


def _database() -> Database:
    database_url = os.environ["DATABASE_URL"]
    configuration = Config(ROOT / "alembic.ini")
    configuration.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(configuration, "head")
    return Database(database_url)


def test_auth_profile_access_origin_and_csrf_share_one_api_boundary(
    tmp_path: Path,
) -> None:
    database = _database()

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    provider = Provider()
    incidents = IncidentService(units, clock=lambda: NOW)
    job_runs = JobRunService(units, incidents, clock=lambda: NOW)
    bus = MessageBus(incidents)
    access_path = tmp_path / "access.toml"
    access_path.write_text('owner_id = "9"\nadmin_ids = []\n', encoding="utf-8")
    users_module = create_users_module(
        units,
        bus,
        provider,
        access_path,
        clock=lambda: NOW,
    )
    access = users_module.access
    auth = users_module.auth
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

    housekeeping_contribution = HousekeepingContribution(
        "operations",
        ("Expired data",),
        no_housekeeping_changes,
    )
    housekeeping = HousekeepingMaintenance((housekeeping_contribution,))
    catalog_module = CatalogModule(
        catalog,
        (),
        housekeeping_contribution,
        LifecycleResource("catalog", close=catalog.close),
    )
    jobs = JobCoordinator(
        job_runs,
        (
            replace(catalog_maintenance.definition(), run_on_startup=False),
            replace(housekeeping.definition(), run_on_startup=False),
        ),
        clock=lambda: NOW,
    )
    player_preparation = prepare_player_module(
        units,
        bus,
        catalog,
        access,
        empty_channel_grace_seconds=60,
    )
    player = player_preparation.service
    listening_module = create_listening_module(
        units,
        bus,
        access,
        lambda: player.state.session.id,
    )
    avatars = DiscordAvatarStore(Path("data/avatars"))
    player_module = complete_player_module(
        player_preparation,
        catalog,
        listening_module.service,
        bus,
        None,
        incidents=incidents,
    )
    statistics_module = create_statistics_module(
        units,
        "UTC",
        access,
        clock=lambda: NOW,
    )
    views_module = create_views_module(units, statistics_module)
    integrations_module = IntegrationsModule(
        identity=provider,
        catalog_providers=(),
        avatars=avatars,
        housekeeping=housekeeping_contribution,
        gateway=cast(DiscordGateway, Gateway()),
        playback_transport=None,
        gateway_lifecycle=None,
    )
    logs = RecentLogBuffer()
    operations_module = OperationsModule(
        incidents=incidents,
        job_runs=job_runs,
        jobs=jobs,
        logs=logs,
        reconciliation_lifecycle=LifecycleResource(
            "job-runs.reconcile",
            start=job_runs.reconcile_interrupted_runs,
        ),
        jobs_lifecycle=LifecycleResource(
            "jobs",
            start=jobs.start,
            close=jobs.close,
        ),
    )
    logs.addFilter(ContextFilter())
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(logs)
    settings = Settings(
        os.environ["DATABASE_URL"],
        AuthSettings(
            ORIGIN,
            "123",
            "secret",
            access_path,
            frozenset({"http://localhost:3001"}),
        ),
    )
    application = Application(
        settings,
        database,
        bus,
        users_module,
        catalog_module,
        player_module,
        listening_module,
        statistics_module,
        views_module,
        integrations_module,
        operations_module,
    )
    app = create_app(application)

    @app.get("/api/_test/broken", include_in_schema=False)
    async def broken() -> None:
        raise RuntimeError("test failure")

    contract = app.openapi()
    operation_ids = [
        operation["operationId"]
        for path in contract["paths"].values()
        for method, operation in path.items()
        if method
        in {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
    ]
    assert all(
        identifier[0].islower() and "_" not in identifier
        for identifier in operation_ids
    )
    assert len(operation_ids) == len(set(operation_ids))
    assert "/api/events" in contract["paths"]
    assert "/api/playbacks" in contract["paths"]
    assert not any(path.startswith("/api/listening") for path in contract["paths"])
    assert "/api/catalog/discoveries/{version}" in contract["paths"]
    assert "/api/catalog/discoveries/{version}/continuations" in contract["paths"]
    assert not any("{kind}" in path for path in contract["paths"])
    assert "/api/logs" in contract["paths"]
    assert "/api/jobs" in contract["paths"]
    assert "/api/jobs/runs" in contract["paths"]
    assert "/api/jobs/runs/{run_id}" in contract["paths"]
    assert "/api/jobs/{job_id}/runs" in contract["paths"]
    assert "/api/jobs/housekeeping" not in contract["paths"]
    assert "/api/incidents" in contract["paths"]
    assert "/api/player/control" in contract["paths"]
    assert "/api/player/voice" in contract["paths"]
    assert "/api/player/radio" in contract["paths"]
    assert "/api/player/sleep-timer" in contract["paths"]
    assert "/api/player/play" not in contract["paths"]
    assert "/api/player/voice/join" not in contract["paths"]
    assert "/api/player/radio/stop" not in contract["paths"]
    assert "/api/statistics" in contract["paths"]
    assert not any(path.startswith("/api/statistics/") for path in contract["paths"])
    assert "/api/users/me/profile" not in contract["paths"]
    assert "/api/users/me/appearance" not in contract["paths"]
    assert "ErrorView" in contract["components"]["schemas"]
    assert contract["paths"]["/api/player"]["get"]["responses"]["401"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ErrorView"}
    assert set(contract["paths"]["/api/player"]["get"]["responses"]) == {
        "200",
        "401",
        "500",
        "503",
    }
    assert set(contract["paths"]["/api/jobs"]["get"]["responses"]) == {
        "200",
        "401",
        "403",
        "500",
        "503",
    }
    assert "502" not in contract["paths"]["/api/jobs/runs"]["get"]["responses"]
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

            overview = await client.get("/api/statistics")
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
            own_profile = await client.get(f"/api/profiles/{current.user.id}")
            assert own_profile.status_code == 200
            assert own_profile.json()["id"] == str(current.user.id)
            assert own_profile.json()["discord"]["display_name"] == "Andrey"
            assert own_profile.json()["discord"]["avatar_url"] == (
                application.integrations.avatars.public_url(
                    "9",
                    source_url="https://cdn.discordapp.com/avatars/9/test.png",
                )
            )
            assert own_profile.json()["statistics"]["user_id"] == str(current.user.id)
            personal_highlights = own_profile.json()["statistics"]["highlights"]
            assert personal_highlights["group_listening_share"] is None
            assert personal_highlights["badges"] == []
            assert len(personal_highlights["listening_pattern"]["weekdays"]) == 7
            assert len(personal_highlights["listening_pattern"]["hours"]) == 24
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
            assert (await client.get("/api/playbacks")).json() == {
                "items": [],
                "contributors": [],
                "page": 1,
                "page_size": 20,
                "total": 0,
                "page_count": 0,
                "snapshot": None,
            }
            invalid_page = await client.get("/api/playbacks", params={"page_size": 0})
            assert invalid_page.status_code == 422
            assert invalid_page.json() == {"code": "validation_failed"}
            events = event_stream(application, session_token)
            initial = await anext(events)
            assert initial.event == "state"
            assert initial.id == "0"
            assert isinstance(initial.data, PlayerView)
            assert initial.data.queue == ()

            rejected = await client.patch(
                "/api/users/me",
                json={"profile": {"display_name": "Owner"}},
            )
            assert rejected.status_code == 403
            assert rejected.json() == {"code": "csrf_failed"}

            incident_report = await client.get("/api/incidents")
            assert incident_report.status_code == 200
            assert incident_report.json()["retention_days"] == 14
            assert incident_report.json()["page"] == 1
            assert incident_report.json()["page_size"] == 20
            assert incident_report.json()["total"] >= 1
            assert "recent" not in incident_report.json()
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
            assert jobs_response.json()["jobs"][0]["controls"] == {
                "batch_size": {"default": 10, "minimum": 1, "maximum": 100},
                "preview": None,
                "age_days": None,
            }
            assert [item["health"] for item in jobs_response.json()["jobs"]] == [
                "unknown",
                "unknown",
            ]
            assert "recent_runs" not in jobs_response.json()
            assert jobs_response.json()["history_retention_days"] == 30
            job_runs = await client.get("/api/jobs/runs")
            assert job_runs.status_code == 200
            assert job_runs.json() == {
                "items": [],
                "page_size": 20,
                "next_cursor": None,
            }
            missing_job_run = await client.get(f"/api/jobs/runs/{uuid4()}")
            assert missing_job_run.status_code == 404
            assert missing_job_run.json() == {
                "code": "not_found",
                "retryable": False,
            }
            invalid_job_cursor = await client.get(
                "/api/jobs/runs",
                params={"cursor": "not-a-cursor"},
            )
            assert invalid_job_cursor.status_code == 422
            assert invalid_job_cursor.json() == {
                "code": "validation_failed",
                "retryable": False,
            }
            first_run = await application.operations.job_runs.start_run(
                JobId.HOUSEKEEPING,
                JobTrigger.MANUAL,
                1,
                current.user.id,
            )
            first_run = await application.operations.job_runs.finish_run(
                first_run,
                candidate_count=1,
                processed_count=1,
                changed_count=1,
            )
            second_run = await application.operations.job_runs.start_run(
                JobId.HOUSEKEEPING,
                JobTrigger.MANUAL,
                1,
                current.user.id,
            )
            second_run = await application.operations.job_runs.finish_run(
                second_run,
                candidate_count=1,
                processed_count=1,
                changed_count=0,
            )
            first_run_page = await client.get(
                "/api/jobs/runs",
                params={"page_size": 1, "job_id": "housekeeping"},
            )
            assert first_run_page.status_code == 200
            assert len(first_run_page.json()["items"]) == 1
            assert "details" not in first_run_page.json()["items"][0]
            assert first_run_page.json()["next_cursor"] is not None
            second_run_page = await client.get(
                "/api/jobs/runs",
                params={
                    "page_size": 1,
                    "job_id": "housekeeping",
                    "cursor": first_run_page.json()["next_cursor"],
                },
            )
            assert second_run_page.status_code == 200
            paged_ids = {
                first_run_page.json()["items"][0]["id"],
                second_run_page.json()["items"][0]["id"],
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
            assert started_job.json()["active_options"] == {
                "batch_size": 3,
                "preview": None,
                "age_days": None,
            }
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
                headers={**headers, "Idempotency-Key": str(operation_id)},
                json={
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert queued.status_code == 200
            assert queued.json()["operation_id"] == str(operation_id)
            assert queued.json()["player"]["queue"][0]["request"][
                "requested_by"
            ] == str(current.user.id)
            contributor = queued.json()["player"]["queue"][0]["request"]["contributor"]
            assert contributor == {
                "user_id": str(current.user.id),
                "display_name": "Owner",
                "discord_id": "9",
                "discord_username": "Owner",
                "avatar_url": application.integrations.avatars.public_url("9"),
            }
            request_id = queued.json()["player"]["queue"][0]["request"]["id"]
            playback_id = uuid4()
            prior_playback_id = uuid4()
            async with units() as work:
                await work.session.execute(
                    insert(Base.metadata.tables["playback_records"]).values(
                        id=playback_id,
                        session_id=application.player.service.state.session.id,
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
                        session_id=application.player.service.state.session.id,
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
            enriched_overview = await client.get("/api/statistics")
            assert enriched_overview.status_code == 200
            top_listener = enriched_overview.json()["top_listeners"][0]
            assert top_listener["discord_username"] == "Owner"
            assert top_listener["discord_display_name"] == "Andrey"
            assert top_listener[
                "avatar_url"
            ] == application.integrations.avatars.public_url(
                "9",
                source_url="https://cdn.discordapp.com/avatars/9/test.png",
            )
            recent = await client.get("/api/playbacks", params={"page_size": 1})
            assert recent.status_code == 200
            recent_payload = recent.json()
            snapshot = recent_payload.pop("snapshot")
            assert snapshot
            assert recent_payload == {
                "items": [
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
                            "avatar_url": application.integrations.avatars.public_url(
                                "9"
                            ),
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
                        "avatar_url": application.integrations.avatars.public_url("9"),
                    }
                ],
                "page": 1,
                "page_size": 1,
                "total": 2,
                "page_count": 2,
            }
            older = await client.get(
                "/api/playbacks",
                params={"page": 2, "page_size": 1, "snapshot": snapshot},
            )
            assert older.status_code == 200
            assert older.json()["items"][0]["playback_id"] == str(prior_playback_id)
            assert older.json()["snapshot"] == snapshot
            search = await client.get("/api/playbacks", params={"q": "api TRACK"})
            assert search.status_code == 200
            assert search.json()["total"] == 2
            manual = await client.get(
                "/api/playbacks",
                params={"radio": False, "requested_by": str(current.user.id)},
            )
            assert manual.status_code == 200
            assert manual.json()["total"] == 2
            assert all(entry["origin"] == "manual" for entry in manual.json()["items"])
            recent_window = await client.get(
                "/api/playbacks",
                params={
                    "started_from": (NOW - timedelta(seconds=30)).isoformat(),
                    "started_to": (NOW + timedelta(seconds=1)).isoformat(),
                    "end_reason": "completed",
                },
            )
            assert recent_window.status_code == 200
            assert recent_window.json()["total"] == 1
            invalid_window = await client.get(
                "/api/playbacks",
                params={
                    "started_from": NOW.isoformat(),
                    "started_to": (NOW - timedelta(seconds=1)).isoformat(),
                },
            )
            assert invalid_window.status_code == 422
            assert invalid_window.json() == {
                "code": "validation_failed",
                "retryable": False,
            }
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
                PlaybackRuntimeChanged(application.player.service.state.session.id)
            )
            runtime = await anext(events)
            assert runtime.event == "state"
            assert runtime.id == "1"
            assert isinstance(runtime.data, PlayerView)
            assert runtime.data.revision == 1
            replayed = await client.post(
                "/api/player/queue",
                headers={**headers, "Idempotency-Key": str(operation_id)},
                json={
                    "tracks": [{"track_id": str(track_id)}],
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["replayed"] is True
            assert len(replayed.json()["player"]["queue"]) == 1

            clear_operation_id = uuid4()
            cleared = await client.post(
                "/api/player/queue/clear",
                headers={**headers, "Idempotency-Key": str(clear_operation_id)},
                json={
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
            undo_operation_id = uuid4()
            restored = await client.post(
                "/api/player/queue/undo",
                headers={**headers, "Idempotency-Key": str(undo_operation_id)},
                json={
                    "undo_id": cleared.json()["outcome"]["undo_id"],
                },
            )
            assert restored.status_code == 200
            assert len(restored.json()["player"]["queue"]) == 1
            await anext(events)

            profile = await client.patch(
                "/api/users/me",
                headers=headers,
                json={
                    "profile": {"display_name": "Local owner"},
                    "appearance": {
                        "mode": "dark",
                        "artwork_colors": True,
                        "primary_color": "cyan",
                        "neutral_color": "zinc",
                        "font_family": "Geist",
                        "icon_set": "lucide",
                        "text_size": "md",
                    },
                },
            )
            assert profile.status_code == 200
            assert profile.json()["profile"]["display_name"] == "Local owner"
            assert profile.json()["discord"]["username"] == "Owner"
            assert profile.json()["appearance"]["primary_color"] == "cyan"
            assert "statistics" not in profile.json()
            appearance = await client.patch(
                "/api/users/me",
                headers=headers,
                json={
                    "appearance": {
                        **profile.json()["appearance"],
                        "primary_color": "teal",
                    }
                },
            )
            assert appearance.status_code == 200
            assert appearance.json()["profile"]["display_name"] == "Local owner"
            assert appearance.json()["appearance"]["primary_color"] == "teal"
            empty_update = await client.patch("/api/users/me", headers=headers, json={})
            assert empty_update.status_code == 422
            assert empty_update.json() == {"code": "validation_failed"}

            play_operation_id = uuid4()
            played = await client.post(
                "/api/player/control",
                headers={**headers, "Idempotency-Key": str(play_operation_id)},
                json={"action": "play"},
            )
            assert played.status_code == 200
            runtime = played.json()["player"]["runtime"]
            assert runtime["phase"] == "starting"
            assert runtime["current"]["id"] == request_id
            assert runtime["current"]["track"]["title"] == "API track"
            assert runtime["current"]["contributor"]["display_name"] == "Local owner"
            assert runtime["current"]["contributor"]["avatar_url"] == (
                application.integrations.avatars.public_url("9")
            )

            operator = await client.put("/api/access/9", headers=headers)
            assert operator.status_code == 409
            assert operator.json() == {"code": "operator_access_managed_in_config"}

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
            assert (
                await client.get("/api/access/members", params={"q": "missing"})
            ).json() == {"members": []}
            assert (
                await client.get("/api/access/members", params={"guild_id": "1"})
            ).json() == members.json()
            granted_user_id = access_state.json()["grants"][0]["user"]["id"]
            granted_profile = await client.get(f"/api/profiles/{granted_user_id}")
            assert granted_profile.status_code == 200

            revoked = await client.delete("/api/access/7", headers=headers)
            assert revoked.status_code == 200
            hidden_profile = await client.get(f"/api/profiles/{granted_user_id}")
            assert hidden_profile.status_code == 404
            assert hidden_profile.json() == {"code": "profile_not_found"}

            process_logs = await client.get("/api/logs", params={"limit": 200})
            assert process_logs.status_code == 200
            entries = process_logs.json()["items"]
            assert entries
            assert any(entry["actor_id"] == str(current.user.id) for entry in entries)
            assert all("q=test" not in entry["message"] for entry in entries)
            assert all("state=" not in entry["message"] for entry in entries)

            wrong_origin = await client.get(
                "/api/users/me", headers={"origin": "https://evil.example"}
            )
            assert wrong_origin.status_code == 403
            assert wrong_origin.json() == {"code": "origin_forbidden"}

            development_origin = await client.patch(
                "/api/users/me",
                headers={
                    "origin": "http://localhost:3001",
                    "x-csrf-token": current.csrf,
                },
                json={"profile": {"display_name": "Local owner"}},
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
            assert rejected_login_origin.json() == {"code": "origin_forbidden"}

            missing = await client.get("/api/_test/missing")
            assert missing.status_code == 404
            assert missing.json() == {"code": "not_found", "retryable": False}

            broken = await client.get("/api/_test/broken")
            assert broken.status_code == 500
            assert broken.json() == {"code": "internal_error", "retryable": True}

            incident_report = await client.get(
                "/api/incidents",
                params={"page_size": 100},
            )
            http_incidents = [
                item
                for item in incident_report.json()["items"]
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
            filtered_incidents = await client.get(
                "/api/incidents",
                params={
                    "severity": "error",
                    "component": "http",
                    "code": "internal_error",
                    "page": 1,
                    "page_size": 1,
                },
            )
            assert filtered_incidents.status_code == 200
            assert filtered_incidents.json()["total"] == 1
            assert filtered_incidents.json()["totals"]["errors"] == 1
            assert filtered_incidents.json()["items"][0]["error_code"] == (
                "internal_error"
            )

            sleep_operation_id = uuid4()
            sleep_timer = await client.put(
                "/api/player/sleep-timer",
                headers={**headers, "Idempotency-Key": str(sleep_operation_id)},
                json={"seconds": 900},
            )
            assert sleep_timer.status_code == 200
            assert sleep_timer.json()["outcome"]["action"] == "sleep_timer.set"
            assert sleep_timer.json()["player"]["sleep_timer_expires_at"] is not None
            await anext(events)
            cancel_sleep_operation_id = uuid4()
            cancelled_sleep_timer = await client.request(
                "DELETE",
                "/api/player/sleep-timer",
                headers={
                    **headers,
                    "Idempotency-Key": str(cancel_sleep_operation_id),
                },
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

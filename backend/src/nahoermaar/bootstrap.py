# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""The application's single composition root."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import perf_counter

from .catalog.main import CatalogModule, create_catalog_module

from .config import Settings
from .database.core import Database
from .database.schema import migrate
from .database.uow import UnitOfWork
from .integrations.main import (
    IntegrationsModule,
    complete_integrations_module,
    prepare_integrations_module,
)
from .lifecycle import LifecycleResource
from .listening.main import ListeningModule, create_listening_module
from .messaging import MessageBus
from .observability import configure_logging
from .operations.main import (
    OperationsModule,
    complete_operations_module,
    create_operations_foundation,
)
from .player.main import (
    PlayerModule,
    complete_player_module,
    prepare_player_module,
)
from .statistics.main import StatisticsModule, create_statistics_module
from .users.main import UsersModule, create_users_module
from .views.main import ViewsModule, create_views_module

_LOGGER = logging.getLogger(__name__)


class ApplicationLifecycle(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"


def _resources() -> list[LifecycleResource]:
    return []


@dataclass(slots=True)
class Application:
    """Dependencies owned by one application process."""

    settings: Settings
    database: Database
    bus: MessageBus
    users: UsersModule
    catalog: CatalogModule
    player: PlayerModule
    listening: ListeningModule
    statistics: StatisticsModule
    views: ViewsModule
    integrations: IntegrationsModule
    operations: OperationsModule
    _lifecycle: ApplicationLifecycle = field(
        default=ApplicationLifecycle.NEW,
        init=False,
        repr=False,
    )
    _lifecycle_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _active_resources: list[LifecycleResource] = field(
        default_factory=_resources,
        init=False,
        repr=False,
    )
    _resource_plan: tuple[LifecycleResource, ...] = field(
        default=(),
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        optional_resources = tuple(
            resource
            for resource in (
                self.integrations.gateway_lifecycle,
                self.player.playback_lifecycle,
            )
            if resource is not None
        )
        self._resource_plan = (
            LifecycleResource("database", close=self.database.close),
            self.catalog.lifecycle,
            self.operations.reconciliation_lifecycle,
            self.users.lifecycle,
            self.player.session_lifecycle,
            self.listening.lifecycle,
            self.player.automation_lifecycle,
            self.operations.jobs_lifecycle,
            *optional_resources,
        )
        names = tuple(resource.name for resource in self._resource_plan)
        if len(names) != len(set(names)):
            raise ValueError("Lifecycle resource names must be unique.")
        self._active_resources.extend(self._resource_plan[:2])

    @property
    def lifecycle(self) -> ApplicationLifecycle:
        return self._lifecycle

    async def start(self) -> None:
        """Migrate storage and reconcile startup-owned state before requests."""
        async with self._lifecycle_lock:
            if self._lifecycle is ApplicationLifecycle.RUNNING:
                return
            if self._lifecycle is not ApplicationLifecycle.NEW:
                raise RuntimeError(
                    f"Application cannot start while {self._lifecycle.value}."
                )
            self._lifecycle = ApplicationLifecycle.STARTING
            started_at = perf_counter()
            _LOGGER.info("application.starting")
            try:
                await migrate(self.database.engine)
                for resource in self._resource_plan[2:]:
                    await self._activate(resource)
            except BaseException:
                _LOGGER.exception("application.start_failed")
                await self._close_owned(suppress=True)
                self._lifecycle = ApplicationLifecycle.CLOSED
                raise
            self._lifecycle = ApplicationLifecycle.RUNNING
            _LOGGER.info(
                "application.started session=%s duration_ms=%.1f",
                self.player.service.state.session.id,
                (perf_counter() - started_at) * 1000,
            )

    async def close(self) -> None:
        """Release process-owned resources."""
        async with self._lifecycle_lock:
            if self._lifecycle is ApplicationLifecycle.CLOSED:
                return
            self._lifecycle = ApplicationLifecycle.CLOSING
            started_at = perf_counter()
            _LOGGER.info("application.closing")
            failures = await self._close_owned(suppress=False)
            self._lifecycle = ApplicationLifecycle.CLOSED
            _LOGGER.info(
                "application.closed duration_ms=%.1f",
                (perf_counter() - started_at) * 1000,
            )
            if failures:
                raise ExceptionGroup("Application shutdown failed.", failures)

    async def _activate(self, resource: LifecycleResource) -> None:
        self._active_resources.append(resource)
        if resource.start is not None:
            await resource.start()

    async def _close_owned(self, *, suppress: bool) -> list[Exception]:
        resources = tuple(reversed(self._active_resources))
        self._active_resources.clear()
        failures: list[Exception] = []
        for resource in resources:
            if resource.close is None:
                continue
            try:
                await resource.close()
            except Exception as error:
                failures.append(error)
                _LOGGER.exception(
                    "application.resource_close_failed resource=%s", resource.name
                )
        if failures and suppress:
            _LOGGER.error(
                "application.start_cleanup_failed resources=%d", len(failures)
            )
        return failures


def bootstrap(
    environ: Mapping[str, str] | None = None,
    *,
    dotenv_path: Path = Path(".env"),
) -> Application:
    """Load configuration and compose the application process."""
    settings = Settings.load(environ, dotenv_path=dotenv_path)
    logs = configure_logging(
        settings.log_level,
        settings.log_directory,
        settings.log_retention_days,
    )
    database = Database(settings.database_url)
    integrations_preparation = prepare_integrations_module(
        settings.auth,
        settings.discord,
        node_path=settings.node_path,
        avatar_directory=settings.avatar_directory,
    )

    def units() -> UnitOfWork:
        return UnitOfWork(database.sessions)

    operations_foundation = create_operations_foundation(units, logs)
    bus = MessageBus(operations_foundation.incidents)
    users_module = create_users_module(
        units,
        bus,
        integrations_preparation.identity,
        settings.auth.access_path,
    )
    access = users_module.access
    statistics_module = create_statistics_module(
        units,
        settings.statistics_timezone,
        access,
    )
    views_module = create_views_module(units, statistics_module)
    catalog_module = create_catalog_module(
        units,
        integrations_preparation.catalog_providers,
        views_module.catalog_cleanup,
    )
    catalog = catalog_module.service
    player_preparation = prepare_player_module(
        units,
        bus,
        catalog,
        access,
        empty_channel_grace_seconds=settings.empty_channel_grace_seconds,
    )
    player = player_preparation.service
    listening_module = create_listening_module(
        units,
        bus,
        access,
        lambda: player.state.session.id,
    )
    integrations_module = complete_integrations_module(
        integrations_preparation,
        player_preparation.summon,
    )
    player_module = complete_player_module(
        player_preparation,
        catalog,
        listening_module.service,
        bus,
        integrations_module.playback_transport,
        incidents=operations_foundation.incidents,
    )
    operations_module = complete_operations_module(
        operations_foundation,
        catalog_module.jobs,
        (
            users_module.housekeeping,
            player_module.housekeeping,
            catalog_module.housekeeping,
            *operations_foundation.housekeeping,
            integrations_module.housekeeping,
        ),
    )
    _LOGGER.info("application.configured")
    return Application(
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

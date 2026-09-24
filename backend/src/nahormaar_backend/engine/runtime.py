# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Composition with explicit dependencies and one owner for each resource."""

import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..application.auth import Auth
from ..application.access import Access
from ..domain.access import DiscordMember
from .audio import AudioPlayer, VoiceTransport
from .catalog import Catalog
from .metadata import MetadataStore
from .persistence import database_engine
from .providers import Provider
from .schema import REVISION
from .session import Session

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Services:
    session: Session
    catalog: Catalog
    metadata: MetadataStore
    voice: VoiceTransport
    auth: Auth
    access: Access
    directory: "MemberDirectory"


class MemberDirectory(Protocol):
    def members(self) -> tuple[DiscordMember, ...]: ...


class EmptyMemberDirectory:
    def members(self) -> tuple[DiscordMember, ...]:
        return ()


@asynccontextmanager
async def open_engine(
    session_id: UUID,
    *,
    auth: Auth,
    providers: tuple[Provider, ...],
    audio: AudioPlayer,
    voice: VoiceTransport,
    directory: MemberDirectory | None = None,
    clock: Callable[[], datetime],
) -> AsyncGenerator[Services]:
    """Take ownership of supplied resources. No environment, migration or login.

    The caller establishes Discord readiness before entering. This composition
    works equally with isolated transports and does not construct a hidden bot.
    """
    started_at = time.monotonic()
    _LOGGER.info(
        "engine.services.opening session=%s providers=%s",
        session_id,
        ",".join(provider.key for provider in providers),
    )
    try:
        async with AsyncExitStack() as resources:
            resources.push_async_callback(auth.close)
            engine = database_engine(auth.settings.database_url)
            resources.push_async_callback(engine.dispose)
            for provider in providers:
                resources.push_async_callback(provider.close)
            resources.push_async_callback(voice.close)
            if id(audio) != id(voice):
                resources.push_async_callback(audio.close)
            async with engine.connect() as connection:
                revision = await connection.run_sync(
                    lambda conn: MigrationContext.configure(conn).get_current_revision()
                )
                if revision != REVISION:
                    raise ValueError(
                        "Initialize an engine database before opening its services."
                    )
            _LOGGER.debug("engine.services.schema_ready revision=%s", revision)
            sessions = async_sessionmaker(
                engine, expire_on_commit=False, autobegin=False
            )
            metadata = MetadataStore(sessions, clock=clock)
            catalog = Catalog(providers, metadata, clock=clock)
            resources.push_async_callback(catalog.close)
            session = await Session.open(
                sessions,
                session_id,
                clock=clock,
                catalog=catalog,
                audio=audio,
                voice=voice,
            )
            resources.push_async_callback(session.close)
            _LOGGER.info(
                "engine.services.opened session=%s elapsed=%.3f",
                session_id,
                time.monotonic() - started_at,
            )
            yield Services(
                session,
                catalog,
                metadata,
                voice,
                auth,
                auth.access,
                directory or EmptyMemberDirectory(),
            )
    finally:
        _LOGGER.info(
            "engine.services.finished session=%s elapsed=%.3f",
            session_id,
            time.monotonic() - started_at,
        )

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Composition with explicit dependencies and one owner for each resource."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..application.auth import Auth
from .audio import AudioPlayer, VoiceTransport
from .catalog import Catalog
from .metadata import MetadataStore
from .persistence import database_engine
from .providers import Provider
from .schema import REVISION
from .session import Session


@dataclass(frozen=True, slots=True)
class Services:
    session: Session
    catalog: Catalog
    metadata: MetadataStore
    voice: VoiceTransport
    auth: Auth


@asynccontextmanager
async def open_engine(
    session_id: UUID,
    *,
    auth: Auth,
    providers: tuple[Provider, ...],
    audio: AudioPlayer,
    voice: VoiceTransport,
    clock: Callable[[], datetime],
) -> AsyncGenerator[Services]:
    """Take ownership of supplied resources. No environment, migration or login.

    The caller establishes Discord readiness before entering. This composition
    works equally with isolated transports and does not construct a hidden bot.
    """
    async with AsyncExitStack() as resources:
        resources.push_async_callback(auth.close)
        engine = database_engine(auth.settings.database_path)
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
                    "Explicitly migrate an isolated database before starting the engine."
                )
        sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)
        metadata = MetadataStore(sessions, clock=clock)
        catalog = Catalog(providers, metadata, clock=clock)
        resources.push_async_callback(catalog.close)
        session = await Session.open(
            sessions, session_id, clock=clock, catalog=catalog, audio=audio, voice=voice
        )
        resources.push_async_callback(session.close)
        yield Services(session, catalog, metadata, voice, auth)

# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Own one cancellable preparation without mutating queue or playback history."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ..domain.crossfade import fade_duration
from .audio import ResolvedTrack, SourceResolver, VoiceError, VoiceOutput

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Preparation:
    attempt_id: UUID
    entry_id: UUID
    seconds: int
    source_url: str
    outgoing_duration: float


class CrossfadePreparation:
    def __init__(self, resolver: SourceResolver, voice: VoiceOutput) -> None:
        self._resolver = resolver
        self._voice = voice
        self.key: Preparation | None = None
        self._task: asyncio.Task[None] | None = None

    async def sync(
        self,
        key: Preparation | None,
        on_due: Callable[[Preparation, ResolvedTrack], None],
        on_failure: Callable[[Preparation, VoiceError], None],
    ) -> None:
        if key == self.key:
            return
        await self.clear()
        if key is not None:
            self.key = key
            self._task = asyncio.create_task(self._prepare(key, on_due, on_failure))

    async def _prepare(
        self,
        key: Preparation,
        on_due: Callable[[Preparation, ResolvedTrack], None],
        on_failure: Callable[[Preparation, VoiceError], None],
    ) -> None:
        try:
            _LOGGER.info(
                "crossfade.preparing attempt=%s entry=%s seconds=%s",
                key.attempt_id,
                key.entry_id,
                key.seconds,
            )
            async with asyncio.timeout(20):
                track = await self._resolver.resolve(key.source_url)
                task = asyncio.current_task()
                if self.key != key or (task is not None and task.cancelling()):
                    return
                seconds = fade_duration(
                    key.seconds, key.outgoing_duration, track.duration_seconds
                )
                if seconds >= 0.02:
                    staged = await self._voice.prepare_next(
                        track, seconds, lambda: on_due(key, track)
                    )
                    _LOGGER.info(
                        "crossfade.preparation_finished attempt=%s entry=%s seconds=%.3f staged=%s",
                        key.attempt_id,
                        key.entry_id,
                        seconds,
                        staged,
                    )
        except asyncio.CancelledError:
            raise
        except VoiceError as error:
            _LOGGER.warning(
                "crossfade.preparation_failed attempt=%s entry=%s error=%s",
                key.attempt_id,
                key.entry_id,
                type(error).__name__,
            )
            on_failure(key, error)
        except Exception as error:
            # A speculative source must never fail or skip the current song.
            # It gets the normal resolver/retry policy when played conventionally.
            _LOGGER.info(
                "crossfade.preparation_unavailable attempt=%s entry=%s error=%s",
                key.attempt_id,
                key.entry_id,
                type(error).__name__,
            )

    async def clear(self) -> None:
        self.key = None
        task, self._task = self._task, None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._voice.discard_next()

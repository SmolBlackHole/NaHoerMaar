# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Time async boundaries without logging media URLs, headers or arguments."""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def logged_operation(
    event: str,
) -> Callable[
    [Callable[P, Coroutine[Any, Any, T]]], Callable[P, Coroutine[Any, Any, T]]
]:
    def decorate(
        operation: Callable[P, Coroutine[Any, Any, T]],
    ) -> Callable[P, Coroutine[Any, Any, T]]:
        logger = logging.getLogger(operation.__module__)

        @wraps(operation)
        async def run(*args: P.args, **kwargs: P.kwargs) -> T:
            started = time.monotonic()
            logger.info("%s.started", event)
            try:
                result = await operation(*args, **kwargs)
            except asyncio.CancelledError:
                logger.info(
                    "%s.cancelled elapsed=%.3f", event, time.monotonic() - started
                )
                raise
            except Exception as error:
                logger.warning(
                    "%s.failed elapsed=%.3f error=%s",
                    event,
                    time.monotonic() - started,
                    type(error).__name__,
                )
                raise
            logger.info("%s.completed elapsed=%.3f", event, time.monotonic() - started)
            return result

        return run

    return decorate

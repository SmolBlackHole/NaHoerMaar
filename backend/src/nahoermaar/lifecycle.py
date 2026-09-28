# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Explicit process lifecycle resources exported by application modules."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

type AsyncLifecycleAction = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class LifecycleResource:
    """One named process resource with optional start and close operations."""

    name: str
    start: AsyncLifecycleAction | None = None
    close: AsyncLifecycleAction | None = None

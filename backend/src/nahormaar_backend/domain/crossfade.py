# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Shared crossfade setting and duration policy."""

from math import isfinite

CROSSFADE_VALUES = (0, 3, 4, 5, 6, 7)


def validate_crossfade(seconds: int) -> None:
    if type(seconds) is not int or seconds not in CROSSFADE_VALUES:
        raise ValueError("Crossfade must be off or between 3 and 7 seconds.")


def fade_duration(
    setting: int, outgoing: float | None, incoming: float | None
) -> float:
    if not setting or outgoing is None or incoming is None:
        return 0.0
    if not isfinite(outgoing) or not isfinite(incoming) or min(outgoing, incoming) <= 0:
        return 0.0
    return min(float(setting), outgoing / 2, incoming / 2)

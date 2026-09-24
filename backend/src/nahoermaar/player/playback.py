# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Pure playback-checkpoint transitions shared by runtime adapters."""

from dataclasses import replace

from .domain import PlaybackCheckpoint, PlaybackIntent, PlayerError, PlayerErrorCode


def pause(checkpoint: PlaybackCheckpoint) -> PlaybackCheckpoint:
    if checkpoint.intent is not PlaybackIntent.PLAYING:
        raise PlayerError(PlayerErrorCode.INVALID_COMMAND, 409)
    return replace(checkpoint, intent=PlaybackIntent.PAUSED)


def resume(checkpoint: PlaybackCheckpoint) -> PlaybackCheckpoint:
    if checkpoint.intent is not PlaybackIntent.PAUSED:
        raise PlayerError(PlayerErrorCode.INVALID_COMMAND, 409)
    return replace(checkpoint, intent=PlaybackIntent.PLAYING)


def seek(checkpoint: PlaybackCheckpoint, seconds: float) -> PlaybackCheckpoint:
    if checkpoint.intent is PlaybackIntent.STOPPED or seconds < 0:
        raise PlayerError(PlayerErrorCode.INVALID_COMMAND, 409)
    return replace(checkpoint, position_seconds=seconds)


def stopped(checkpoint: PlaybackCheckpoint) -> PlaybackCheckpoint:
    return PlaybackCheckpoint(checkpoint.session_id)

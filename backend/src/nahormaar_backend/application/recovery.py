# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Preserve restart intent when the authoritative player snapshot changes."""

from dataclasses import replace

from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.models import PlaybackState, PlayerSnapshot


def reconcile_checkpoint(
    snapshot: PlayerSnapshot, checkpoint: PlaybackCheckpoint | None
) -> PlaybackCheckpoint | None:
    if checkpoint is None or snapshot.state is PlaybackState.ERROR:
        return None
    entry_id = snapshot.current.id if snapshot.current else None
    if checkpoint.entry_id != entry_id:
        checkpoint = replace(
            checkpoint,
            entry_id=entry_id,
            position_seconds=0,
            paused=False,
            history_recorded=False,
        )
    if snapshot.state is not PlaybackState.LOADING:
        checkpoint = replace(checkpoint, paused=snapshot.state is PlaybackState.PAUSED)
    return checkpoint

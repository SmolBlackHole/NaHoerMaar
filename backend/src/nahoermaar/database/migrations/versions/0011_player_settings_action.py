# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Consolidate persisted Player settings outcomes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_player_settings_action"
down_revision: str | None = "0010_source_revalidation_job"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ACTIONS = (
    "queue.added",
    "queue.removed",
    "queue.moved",
    "queue.cleared",
    "queue.restored",
    "radio.started",
    "radio.stopped",
    "radio.retried",
    "radio.filled",
    "radio.failed",
    "playback.played",
    "playback.paused",
    "playback.skipped",
    "playback.stopped",
    "playback.seeked",
    "playback.completed",
    "playback.failed",
    "playback.checkpointed",
    "playback.suspended",
    "playback.volume_changed",
    "playback.crossfade_changed",
    "sleep_timer.set",
    "sleep_timer.cancelled",
    "voice.joined",
    "voice.left",
)
_ACTIONS = (
    *(
        action
        for action in _OLD_ACTIONS
        if action not in {"playback.volume_changed", "playback.crossfade_changed"}
    ),
    "player.settings_updated",
)


def _action_constraint(actions: tuple[str, ...]) -> str:
    values = ", ".join(f"'{value}'" for value in actions)
    return f"action IN ({values})"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        type_="check",
    )
    op.execute(
        sa.text(
            "UPDATE operation_receipts SET action = 'player.settings_updated' "
            "WHERE action IN "
            "('playback.volume_changed', 'playback.crossfade_changed')"
        )
    )
    op.alter_column(
        "operation_receipts",
        "action",
        existing_type=sa.String(length=26),
        type_=sa.String(length=23),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        _action_constraint(_ACTIONS),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        type_="check",
    )
    op.execute(
        sa.text(
            "DELETE FROM operation_receipts WHERE action = 'player.settings_updated'"
        )
    )
    op.alter_column(
        "operation_receipts",
        "action",
        existing_type=sa.String(length=23),
        type_=sa.String(length=26),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        _action_constraint(_OLD_ACTIONS),
    )

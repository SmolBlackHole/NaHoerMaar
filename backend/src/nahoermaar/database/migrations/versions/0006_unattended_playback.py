# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Persist sleep timers and unattended playback outcomes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_unattended_playback"
down_revision: str | None = "0005_operational_incidents"
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
    "playback.volume_changed",
    "playback.crossfade_changed",
    "voice.joined",
    "voice.left",
)
_ACTIONS = (
    *_OLD_ACTIONS,
    "playback.suspended",
    "sleep_timer.set",
    "sleep_timer.cancelled",
)


def _action_constraint(actions: tuple[str, ...]) -> str:
    values = ", ".join(f"'{value}'" for value in actions)
    return f"action IN ({values})"


def upgrade() -> None:
    op.add_column(
        "listening_sessions",
        sa.Column("sleep_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        _action_constraint(_ACTIONS),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM operation_receipts "
            "WHERE action IN "
            "('playback.suspended', 'sleep_timer.set', 'sleep_timer.cancelled')"
        )
    )
    op.drop_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_operation_receipts_player_action"),
        "operation_receipts",
        _action_constraint(_OLD_ACTIONS),
    )
    op.drop_column("listening_sessions", "sleep_at")

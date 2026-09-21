# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Storage required by the single-owner player transaction boundary."""

from typing import Protocol
from uuid import UUID

from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.commands import Receipt, Revisions
from ..domain.models import PlayerSnapshot
from ..domain.undo import Removal


class StorageError(RuntimeError):
    """The player database could not be opened, read or committed."""


class PlayerStore(Protocol):
    def load(self) -> PlayerSnapshot: ...

    def save(
        self,
        snapshot: PlayerSnapshot,
        *,
        checkpoint: PlaybackCheckpoint | None,
        revisions: Revisions | None = None,
        receipt: Receipt | None = None,
    ) -> None: ...

    def revisions(self) -> Revisions: ...

    def checkpoint(self) -> PlaybackCheckpoint | None: ...

    def save_checkpoint(self, checkpoint: PlaybackCheckpoint | None) -> None: ...

    def reserve(self, receipt: Receipt) -> Receipt | None: ...

    def removal(self, undo_id: UUID, actor_id: UUID | None) -> Removal: ...

    def finish(self, receipt: Receipt) -> None: ...

    def interrupt_requests(self) -> None: ...

    def close(self) -> None: ...

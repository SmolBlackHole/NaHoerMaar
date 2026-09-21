# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

"""Single-owner player service. Publish state only after its storage commit."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from ..domain import queue
from ..domain.commands import Receipt, Revisions
from ..domain.checkpoint import PlaybackCheckpoint
from ..domain.fsm import LifecycleEvent, PlaybackContext, decide_playback
from ..domain.models import (
    HISTORY_LIMIT,
    HistoryEntry,
    PlayerSnapshot,
    QueueEntry,
    TrackMetadata,
)
from ..domain.undo import Removal
from .metadata import merge_metadata
from .recovery import reconcile_checkpoint
from .storage import PlayerStore


class Player:
    def __init__(self, store: PlayerStore) -> None:
        self._store = store
        stored = store.load()
        self._snapshot: PlayerSnapshot = stored
        self._revisions = store.revisions()
        self._receipt: Receipt | None = None
        checkpoint = store.checkpoint()
        recovered = decide_playback(
            stored, PlaybackContext(), LifecycleEvent.RECOVER, checkpoint=checkpoint
        )
        self._commit(recovered.snapshot)

    @property
    def checkpoint(self) -> PlaybackCheckpoint | None:
        return self._store.checkpoint()

    def save_checkpoint(self, checkpoint: PlaybackCheckpoint | None) -> None:
        if checkpoint != self._store.checkpoint():
            self._store.save_checkpoint(checkpoint)

    @property
    def revisions(self) -> Revisions:
        return self._revisions

    @property
    def snapshot(self) -> PlayerSnapshot:
        return self._snapshot

    def _commit(self, snapshot: PlayerSnapshot) -> PlayerSnapshot:
        return self.commit_lifecycle(
            snapshot, reconcile_checkpoint(snapshot, self._store.checkpoint())
        )

    def commit_lifecycle(
        self,
        snapshot: PlayerSnapshot,
        checkpoint: PlaybackCheckpoint | None,
        *,
        record_history: bool = False,
    ) -> PlayerSnapshot:
        """Commit a decided lifecycle state and its exact restart intent atomically."""
        if record_history and snapshot.current is not None:
            item = HistoryEntry(snapshot.current, datetime.now(UTC))
            snapshot = replace(
                snapshot,
                recently_played=(item, *snapshot.recently_played)[:HISTORY_LIMIT],
            )
        if snapshot != self._snapshot or checkpoint != self._store.checkpoint():
            versions = Revisions(
                self._revisions.revision + 1,
                self._revisions.queue_revision
                + (
                    tuple(entry.id for entry in snapshot.upcoming)
                    != tuple(entry.id for entry in self._snapshot.upcoming)
                ),
            )
            self._store.save(
                snapshot,
                checkpoint=checkpoint,
                revisions=versions,
                receipt=self._receipt,
            )
            self._revisions = versions
            self._snapshot = snapshot
        elif self._receipt is not None:
            self._store.finish(self._receipt)
        return self._snapshot

    def reserve(self, receipt: Receipt) -> Receipt | None:
        return self._store.reserve(receipt)

    def recover_requests(self) -> None:
        self._store.interrupt_requests()

    def removal(self, undo_id: UUID, actor_id: UUID | None) -> Removal:
        return self._store.removal(undo_id, actor_id)

    def restore_removal(self, removal: Removal) -> PlayerSnapshot:
        return self._commit(queue.restore(self._snapshot, removal))

    def apply_request(
        self, receipt: Receipt, operation: Callable[["Player"], PlayerSnapshot]
    ) -> PlayerSnapshot:
        """Commit a queue operation and its successful outcome together."""
        self._receipt = receipt
        try:
            return operation(self)
        finally:
            self._receipt = None

    def publish(
        self, previous_revision: int, changed: bool, receipt: Receipt | None = None
    ) -> Revisions:
        """Assign runtime-only changes a durable revision before publication."""
        if changed and self._revisions.revision == previous_revision:
            versions = replace(self._revisions, revision=previous_revision + 1)
            self._store.save(
                self._snapshot,
                checkpoint=reconcile_checkpoint(
                    self._snapshot, self._store.checkpoint()
                ),
                revisions=versions,
                receipt=receipt,
            )
            self._revisions = versions
        elif receipt is not None:
            self._store.finish(receipt)
        return self._revisions

    def enqueue(self, entry: QueueEntry) -> PlayerSnapshot:
        return self.enqueue_many((entry,))

    def enqueue_many(self, entries: tuple[QueueEntry, ...]) -> PlayerSnapshot:
        return self._commit(queue.enqueue(self._snapshot, entries))

    def enrich(self, entry_id: UUID, metadata: TrackMetadata) -> PlayerSnapshot:
        """Merge public metadata only while the entry still belongs to the queue."""

        def enrich_entry(entry: QueueEntry) -> QueueEntry:
            return merge_metadata(entry, metadata) if entry.id == entry_id else entry

        return self._commit(
            replace(
                self._snapshot,
                current=enrich_entry(self._snapshot.current)
                if self._snapshot.current
                else None,
                upcoming=tuple(
                    enrich_entry(entry) for entry in self._snapshot.upcoming
                ),
            )
        )

    def remove(self, entries: tuple[QueueEntry, ...]) -> PlayerSnapshot:
        return self._commit(queue.remove(self._snapshot, entries))

    def move_before(
        self, entry_id: UUID, before_entry_id: UUID | None = None
    ) -> PlayerSnapshot:
        return self._commit(
            queue.move_before(self._snapshot, entry_id, before_entry_id)
        )

    def set_crossfade(self, seconds: int) -> PlayerSnapshot:
        return self._commit(replace(self._snapshot, crossfade_seconds=seconds))

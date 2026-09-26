import { onScopeDispose, watch } from "vue";

/** Notifications for mutations owned by this browser and SSE connection changes. */
export function usePlayerNotifications() {
	const player = useNuxtApp().$backendCore.stores.usePlayerStore();
	const toast = useToast();
	let errorToastId: string | number | undefined;
	let connectionToastId: string | number | undefined;
	let operationId: string | undefined;
	let undoTimer: ReturnType<typeof setTimeout> | undefined;

	watch(
		() => player.lastOperation,
		(operation) => {
			if (!operation?.own || operation.operationId === operationId) return;
			operationId = operation.operationId;
			const outcome = operation.outcome;
			const count =
				outcome.added_count ||
				outcome.removed_count ||
				outcome.restored_count ||
				outcome.skipped_count;
			const titles: Record<string, string> = {
				"queue.added": `${count} ${count === 1 ? "track" : "tracks"} added`,
				"queue.removed": "Track removed",
				"queue.cleared": `${count} ${count === 1 ? "track" : "tracks"} removed`,
				"queue.restored": `${count} ${count === 1 ? "track" : "tracks"} restored`,
				"queue.moved": "Queue order updated",
			};
			const title = titles[operation.action];
			if (!title) return;
			const undoId = outcome.undo_id;
			const remaining =
				undoId && outcome.undo_expires_at
					? Math.max(
							0,
							Math.min(12_000, Date.parse(outcome.undo_expires_at) - Date.now()),
						)
					: 0;
			const id = undoId ? `undo-${undoId}` : `operation-${operation.operationId}`;
			toast.add({
				id,
				title,
				description: outcome.skipped_count
					? `${outcome.skipped_count} duplicate requests skipped`
					: undefined,
				duration: remaining || 5000,
				actions:
					remaining && undoId
						? [{ label: "Undo", onClick: () => void player.undo(undoId) }]
						: [],
			});
			clearTimeout(undoTimer);
			if (remaining) undoTimer = setTimeout(() => toast.remove(id), remaining);
		},
		{ flush: "sync" },
	);

	watch(
		() => player.error,
		(description) => {
			if (errorToastId !== undefined) toast.remove(errorToastId);
			if (!description) return;
			errorToastId = toast.add({
				title: player.uncertainOperation ? "Response not received" : "Action not completed",
				description,
				color: "warning",
				duration: player.uncertainOperation ? 0 : 7000,
				actions: player.uncertainOperation
					? [{ label: "Check result", onClick: () => void player.retryUncertain() }]
					: [],
			}).id;
		},
	);

	watch(
		() => player.connection,
		(state, previous) => {
			if (state === "live" && connectionToastId !== undefined) {
				toast.remove(connectionToastId);
				connectionToastId = undefined;
			} else if (state === "reconnecting" && previous === "live") {
				connectionToastId = toast.add({
					title: "Connection lost",
					description: "Controls return once the player is in sync.",
					color: "warning",
				}).id;
			}
		},
	);

	onScopeDispose(() => {
		clearTimeout(undoTimer);
		if (errorToastId !== undefined) toast.remove(errorToastId);
		if (connectionToastId !== undefined) toast.remove(connectionToastId);
	});
}

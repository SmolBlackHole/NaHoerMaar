import { watch, onScopeDispose } from "vue";
import { trackTitle, type MutationResult, type QueueEntry } from "../../shared/player";
import { usePlayerStore } from "../stores/player";

/** Shared shell notifications, independent of page navigation. */
export function usePlayerNotifications() {
	const player = usePlayerStore();
	const toast = useToast();
	const seen = new Set<string>();
	const undoTimers = new Map<string, ReturnType<typeof setTimeout>>();
	let errorToastId: string | number | undefined;
	let connectionToastId: string | number | undefined;
	const actionTitle = (result: MutationResult, n: number, verb: string) =>
		`${result.actor?.name || "You"} ${verb} ${n === 1 ? "a track" : `${n} tracks`}`;
	function trackDescription(entries: QueueEntry[], skipped = 0) {
		const names = entries.slice(0, 2).map((entry) => entry.title || "Title unavailable");
		if (entries.length > 2) names.push(`and ${entries.length - 2} more`);
		if (skipped) names.push(`${skipped} duplicates skipped`);
		return names.join(" · ") || undefined;
	}
	watch(
		() => player.activity,
		(activity) => {
			if (!activity || activity.action === "session.updated" || seen.has(activity.id)) return;
			seen.add(activity.id);
			const { action, result, own, target } = activity;
			if (["radio.started", "radio.stopped", "radio.retried"].includes(action)) {
				toast.add({
					title: `${result.actor?.name || "A listener"} ${action === "radio.stopped" ? "ended" : action === "radio.retried" ? "retried" : "started"} the radio`,
					description: player.snapshot?.radio.title ?? undefined,
				});
				return;
			}
			if (action === "queue.reordered") {
				if (own) toast.add({ title: "Queue order updated" });
				return;
			}
			if (
				!["queue.added", "queue.removed", "queue.cleared", "queue.restored"].includes(
					action,
				)
			)
				return;
			if (action === "queue.restored" && target) {
				const id = `undo-${target}`;
				clearTimeout(undoTimers.get(id));
				undoTimers.delete(id);
				toast.remove(id);
			}
			const verb =
				action === "queue.restored"
					? "restored"
					: action === "queue.added"
						? "added"
						: "removed";
			const count = result.removed_count || result.restored_count || result.added_count;
			const remaining =
				own && result.undo_id && result.undo_expires_at
					? Math.max(0, Math.min(12_000, Date.parse(result.undo_expires_at) - Date.now()))
					: 0;
			const id = remaining ? `undo-${result.undo_id}` : `request-${activity.id}`;
			toast.add({
				id,
				title: actionTitle(result, count, verb),
				description: trackDescription(result.entries, result.skipped_count),
				duration: remaining || 7000,
				actions: remaining
					? [
							{
								label: "Undo",
								onClick: () => {
									if (player.enabled) void player.undo(result.undo_id!);
								},
							},
						]
					: [],
			});
			if (remaining)
				undoTimers.set(
					id,
					setTimeout(() => {
						toast.remove(id);
						undoTimers.delete(id);
					}, remaining),
				);
		},
		{ flush: "sync" },
	);

	watch(
		() => player.error,
		(description) => {
			if (errorToastId !== undefined) toast.remove(errorToastId);
			if (description)
				errorToastId = toast.add({
					title: player.uncertain ? "Response not received" : "Action not completed",
					description,
					color: "warning",
					duration: player.uncertain ? 0 : 7000,
					actions: player.uncertain
						? [
								{
									label: "Check result",
									disabled: player.connection !== "live" || player.pending,
									onClick: () => {
										void player.retry();
									},
								},
							]
						: [],
				}).id;
		},
	);

	watch(
		() => player.snapshot?.last_issue,
		(issue) => {
			if (!issue || seen.has(issue.id)) return;
			seen.add(issue.id);
			const entry = issue.entry;
			const reason = entry
				? "The audio source couldn't be opened."
				: "Rejoin a voice channel and press play to continue.";
			toast.add({
				id: `playback-${issue.id}`,
				title: entry ? "Track skipped" : "Discord connection lost",
				description: entry ? `${trackTitle(entry)}\n${reason}` : reason,
				ui: { description: "whitespace-pre-line" },
				color: "warning",
				duration: 10000,
				actions: entry
					? [
							{
								label: "Add to queue again",
								disabled: !player.enabled,
								onClick: () => {
									void player.addMany([entry.track_id]);
								},
							},
						]
					: [],
			});
		},
	);

	watch(
		() => player.connection,
		(state, previous) => {
			if (state === "live" && connectionToastId !== undefined)
				toast.remove(connectionToastId);
			else if (state === "offline" && previous === "live" && player.snapshot)
				connectionToastId = toast.add({
					title: "Connection lost",
					description: "Controls return once the player is in sync.",
					color: "warning",
				}).id;
		},
	);

	watch(
		() => [player.pending, player.connection, player.enabled],
		() => {
			if (
				player.uncertain &&
				errorToastId !== undefined &&
				toast.toasts.value.some((item) => item.id === errorToastId && item.open !== false)
			)
				toast.update(errorToastId, {
					duration: 0,
					actions: [
						{
							label: "Check result",
							disabled: player.pending || player.connection !== "live",
							loading: player.pending,
							onClick: () => {
								void player.retry();
							},
						},
					],
				});
			for (const id of undoTimers.keys()) {
				const existing = toast.toasts.value.find((item) => item.id === id);
				if (existing?.actions && existing.open !== false)
					toast.update(id, {
						duration: existing.duration,
						actions: existing.actions.map((action) => ({
							...action,
							disabled: !player.enabled,
							loading: player.isPending("queue.restored", id.slice(5)),
						})),
					});
			}
			for (const existing of toast.toasts.value)
				if (
					String(existing.id).startsWith("playback-") &&
					existing.open !== false &&
					existing.actions?.length
				)
					toast.update(existing.id, {
						duration: existing.duration,
						actions: existing.actions.map((action) => ({
							...action,
							disabled: !player.enabled,
						})),
					});
		},
	);

	function clear() {
		for (const [id, timer] of undoTimers) {
			clearTimeout(timer);
			toast.remove(id);
		}
		undoTimers.clear();
		for (const issue of seen) {
			toast.remove(`playback-${issue}`);
			toast.remove(`radio-${issue}`);
		}
		seen.clear();
		if (errorToastId !== undefined) toast.remove(errorToastId);
		if (connectionToastId !== undefined) toast.remove(connectionToastId);
	}
	watch(
		() => player.snapshot,
		(state) => {
			if (!state) clear();
		},
	);
	onScopeDispose(clear);
}

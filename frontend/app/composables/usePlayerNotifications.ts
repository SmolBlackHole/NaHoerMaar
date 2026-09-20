import { watch, onScopeDispose } from "vue";
import {
	trackTitle,
	youtubeVideoId,
	type MutationResult,
	type QueueEntry,
} from "../../shared/player";
import { usePlayerStore } from "../stores/player";

/** Shared shell notifications, independent of page navigation. */
export function usePlayerNotifications() {
	const player = usePlayerStore();
	const toast = useToast();
	const seen = new Set<string>();
	const undoTimers = new Map<string, ReturnType<typeof setTimeout>>();
	const pendingTitles = new Map<string, { entries: QueueEntry[]; skipped: number }>();
	let errorToastId: string | number | undefined;
	let connectionToastId: string | number | undefined;
	const actionTitle = (result: MutationResult, n: number, verb: string) =>
		`${result.actor?.name || "You"} ${verb} ${n === 1 ? "a track" : `${n} tracks`}`;
	watch(
		() => player.snapshot?.radio,
		(radio, previous) => {
			if (
				!radio?.event_id ||
				!previous ||
				radio.event_id === previous.event_id ||
				seen.has(radio.event_id)
			)
				return;
			seen.add(radio.event_id);
			toast.add({
				id: `radio-${radio.event_id}`,
				title: radio.actor
					? `${radio.actor.name} ${radio.action === "stopped" ? "ended" : radio.action === "retried" ? "retried" : "started"} the radio`
					: "Radio ended",
				description: radio.seed?.title,
			});
		},
	);
	function trackDescription(entries: QueueEntry[], skipped = 0, loading = false) {
		const names = entries
			.slice(0, 2)
			.map((entry) => entry.title || (loading ? "Title is loading…" : "Title unavailable"));
		if (entries.length > 2) names.push(`and ${entries.length - 2} more`);
		if (skipped) names.push(`${skipped} duplicates skipped`);
		return names.join(" · ") || undefined;
	}
	function addedToast(result: MutationResult, id: string) {
		const entries = result.entries;
		toast.add({
			id,
			title: actionTitle(result, result.added_count, "added"),
			description: trackDescription(entries, result.skipped_count, true),
			duration: 7000,
		});
		if (entries.some((entry) => !entry.title))
			pendingTitles.set(id, { entries, skipped: result.skipped_count });
	}

	watch(
		() => player.completed,
		(completed) => {
			if (!completed || seen.has(completed.request.id)) return;
			seen.add(completed.request.id);
			const { request, result } = completed;
			for (const id of pendingTitles.keys())
				if (!toast.toasts.value.some((item) => item.id === id && item.open !== false))
					pendingTitles.delete(id);
			if (result.undo_id && result.undo_expires_at) {
				const id = `undo-${result.undo_id}`;
				const remaining = Math.max(0, Date.parse(result.undo_expires_at) - Date.now());
				toast.add({
					id,
					title: actionTitle(result, result.removed_count, "removed"),
					description: trackDescription(result.entries),
					duration: remaining || 5000,
					actions: remaining
						? [
								{
									label: "Undo",
									onClick: () => {
										if (!player.enabled) return;
										void player.mutate("/api/queue/undo", "POST", {
											undo_id: result.undo_id,
										});
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
			} else if (request.path === "/api/queue/batch" || request.path === "/api/queue") {
				addedToast(result, `request-${request.id}`);
			} else if (request.path === "/api/queue/undo") {
				for (const [id, timer] of undoTimers) {
					if (request.body !== JSON.stringify({ undo_id: id.slice(5) })) continue;
					clearTimeout(timer);
					undoTimers.delete(id);
					toast.remove(id);
				}
				toast.add({
					title: actionTitle(result, result.restored_count, "restored"),
					description: trackDescription(result.entries),
				});
			} else if (request.path.endsWith("/move")) {
				toast.add({ title: "Queue order updated" });
			} else if (
				request.path === "/api/queue/clear" ||
				(request.method === "DELETE" && request.path.startsWith("/api/queue/"))
			) {
				toast.add({
					title: actionTitle(result, result.removed_count, "removed"),
					description: trackDescription(result.entries),
				});
			}
		},
	);

	watch([() => player.snapshot, () => toast.toasts.value], ([state]) => {
		if (!state) return;
		const known = [
			...(state.current ? [state.current] : []),
			...state.upcoming,
			...state.recently_played.map((item) => item.entry),
		];
		for (const [id, pending] of pendingTitles) {
			const existing = toast.toasts.value.find((item) => item.id === id);
			if (!existing) continue; // Nuxt UI inserts queued toasts on the next tick.
			if (existing.open === false) {
				pendingTitles.delete(id);
				continue;
			}
			pending.entries = pending.entries.map((entry) => {
				if (entry.title) return entry;
				const video = entry.video_id || youtubeVideoId(entry.source_url);
				return (
					known.find(
						(candidate) =>
							candidate.title &&
							(candidate.id === entry.id ||
								(video &&
									(candidate.video_id || youtubeVideoId(candidate.source_url)) ===
										video)),
					) || entry
				);
			});
			const description = trackDescription(pending.entries, pending.skipped, true);
			if (description !== existing.description)
				toast.update(id, { description, duration: existing.duration });
			if (pending.entries.every((entry) => entry.title)) pendingTitles.delete(id);
		}
	});

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
			const reason = issue.fatal
				? "The bot needs a restart before playback can continue."
				: issue.reason === "stream_interrupted"
					? "The audio stream stopped, and retrying didn't help."
					: entry
						? "The audio source couldn't be opened."
						: "Rejoin a voice channel and press play to continue.";
			toast.add({
				id: `playback-${issue.id}`,
				title: issue.fatal
					? "Playback stopped"
					: entry
						? "Track skipped"
						: "Discord connection lost",
				description: entry ? `${trackTitle(entry)}\n${reason}` : reason,
				ui: { description: "whitespace-pre-line" },
				color: issue.fatal ? "error" : "warning",
				duration: 10000,
				actions:
					entry && !issue.fatal
						? [
								{
									label: "Add to queue again",
									disabled: !player.enabled,
									onClick: () => {
										void player.add(entry.source_url);
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
							loading:
								player.isPending("/api/queue/undo") &&
								player.activeRequest?.body ===
									JSON.stringify({ undo_id: id.slice(5) }),
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
		pendingTitles.clear();
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

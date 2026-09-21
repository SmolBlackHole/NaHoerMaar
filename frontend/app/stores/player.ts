import { computed, ref, shallowRef, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { ApiFailure, SessionLost } from "../repositories/transport";
import { useRepositories } from "../repositories";
import { useProfileStore } from "./profile";
import {
	presentSession,
	presentTrack,
	type SessionState,
	type MutationReply,
	type SessionChange,
	type SessionAction,
	type MediaReference,
	type VoiceChannel,
} from "../../shared/engine";
import type { MutationResult, PlaybackAction } from "../../shared/player";

export interface PendingRequest {
	id: string;
	action: SessionAction;
	target?: string;
	trackIds?: readonly string[];
	execute: (id: string) => Promise<MutationReply>;
}
export interface PlayerActivity {
	id: string;
	action: SessionAction;
	result: MutationResult;
	own: boolean;
	target?: string;
}
const messages: Record<string, string> = {
	invalid_request: "The request is invalid. Check your selection and try again.",
	radio_conflict: "The radio changed. Check the current radio and try again.",
	not_connected: "Join a voice channel before controlling playback.",
	nothing_playing: "There is no current track to control.",
	invalid_seek: "That position is outside the current track.",
	undo_unavailable: "Undo has expired or was already used. The queue is unchanged.",
	queue_conflict: "The queue changed. Check the updated queue and try again.",
	playback_conflict: "The track changed before your action arrived. The player is up to date.",
	entry_not_found: "That track has already left the queue.",
	invalid_action: "That action is no longer available. Check the current player state.",
	voice_unavailable:
		"Couldn't connect to Discord. Check the channel and bot permissions, then try again.",
	backend_unavailable: "The bot is unavailable. Wait for it to reconnect.",
	interrupted: "The bot restarted during this action. Check the queue before trying again.",
	idempotency_conflict:
		"The request couldn't be repeated. Check the player before sending another action.",
};

export const usePlayerStore = defineStore("player", () => {
	const { session: api, catalog } = useRepositories();
	const profile = useProfileStore();
	const session = shallowRef<SessionState | null>(null);
	const positionUpdatedAt = ref("");
	const snapshot = computed(() =>
		session.value ? presentSession(session.value, positionUpdatedAt.value) : null,
	);
	const connection = ref<"connecting" | "live" | "offline">("connecting");
	const channels = shallowRef<VoiceChannel[]>([]);
	const channelError = ref(false);
	const channelsLoading = ref(false);
	const pending = ref(false);
	const activeRequest = shallowRef<PendingRequest | null>(null);
	const activity = shallowRef<PlayerActivity | null>(null);
	const resolving = ref<string | null>(null);
	const uncertain = shallowRef<PendingRequest | null>(null);
	const error = ref<string | null>(null);
	const enabled = computed(
		() =>
			connection.value === "live" &&
			!!session.value &&
			!pending.value &&
			!resolving.value &&
			!uncertain.value,
	);
	const settled = new Map<string, boolean>();
	let disconnect: (() => void) | undefined;
	let generation = 0;

	function accept(state: SessionState) {
		if (!Number.isSafeInteger(state.session?.revision) || !Array.isArray(state.queue))
			throw new Error("Invalid session state");
		const previous = session.value;
		if (
			previous?.session.id === state.session.id &&
			state.session.revision < previous.session.revision
		)
			return;
		if (
			previous?.session.id !== state.session.id ||
			previous.checkpoint.play_id !== state.checkpoint.play_id ||
			previous.checkpoint.position_seconds !== state.checkpoint.position_seconds ||
			previous.checkpoint.intent !== state.checkpoint.intent ||
			previous.playback.attempt_id !== state.playback.attempt_id ||
			previous.playback.phase !== state.playback.phase
		)
			positionUpdatedAt.value = new Date().toISOString();
		session.value = state;
	}

	function publish(reply: SessionChange | MutationReply, own: boolean, command?: PendingRequest) {
		const { state, outcome } = reply;
		const id = reply.request_id ?? `${state.session.id}:${state.session.revision}`;
		if (settled.has(id)) return;
		settled.set(id, outcome.code === "ok");
		if (settled.size > 256) settled.delete(settled.keys().next().value!);
		if (uncertain.value?.id === id) {
			uncertain.value = null;
			error.value =
				outcome.code === "ok"
					? null
					: (messages[outcome.code] ?? "The action was rejected.");
		}
		if (outcome.code !== "ok") return;
		activity.value = {
			id,
			action: reply.action,
			own,
			target: command?.target,
			result: {
				...outcome,
				replayed: "replayed" in reply && reply.replayed,
				entries: outcome.entries.map((entry) =>
					presentTrack(state.tracks[entry.track_id], entry),
				),
			},
		};
	}

	async function refreshChannels() {
		if (channelsLoading.value) return;
		channelsLoading.value = true;
		const version = generation;
		try {
			const values = await api.channels();
			if (version !== generation) return;
			channels.value = values;
			channelError.value = false;
		} catch {
			if (version === generation) channelError.value = true;
		} finally {
			if (version === generation) channelsLoading.value = false;
		}
	}

	function connect() {
		disconnect?.();
		connection.value = "connecting";
		disconnect = api.subscribe((event) => {
			if (event.type === "auth") {
				dispose();
				profile.lost(event.code);
			} else if (event.type === "offline") {
				connection.value = "offline";
				void profile.restore();
			} else if (event.type === "connecting") {
				connection.value = "connecting";
			} else if (event.type === "state" || event.type === "change") {
				accept(event.type === "state" ? event.data : event.data.state);
				if (event.type === "change") {
					const value = event.data;
					const command = [activeRequest.value, uncertain.value].find(
						(item) => item?.id === value.request_id,
					);
					publish(
						value,
						!!command ||
							(!!value.outcome.actor &&
								value.outcome.actor.id === profile.profile?.id),
						command ?? undefined,
					);
				}
				const reconnected = connection.value !== "live";
				connection.value = "live";
				if (reconnected) void refreshChannels();
			}
		});
	}

	async function send(command: PendingRequest): Promise<boolean> {
		const version = generation;
		pending.value = true;
		activeRequest.value = command;
		error.value = null;
		try {
			const result = await command.execute(command.id);
			if (version !== generation) return false;
			if (result.request_id !== command.id || result.action !== command.action)
				throw new Error("Mismatched command response");
			accept(result.state);
			uncertain.value = null;
			publish(result, true, command);
			if (result.outcome.code !== "ok") {
				error.value = messages[result.outcome.code] ?? "The action was rejected.";
				return false;
			}
			return true;
		} catch (failure) {
			if (version !== generation || failure instanceof SessionLost) return false;
			if (
				failure instanceof ApiFailure &&
				("state" in failure.data || failure.status < 500)
			) {
				const data = failure.data;
				if ("state" in data) accept(data.state);
				uncertain.value = null;
				const code = "outcome" in data ? data.outcome.code : data.code;
				error.value =
					messages[code] ??
					"The request was rejected. Check your selection and try again.";
			} else if (settled.has(command.id)) {
				// Live events can confirm an operation even if its HTTP response was lost.
				return settled.get(command.id)!;
			} else {
				uncertain.value = command;
				error.value =
					"The response was lost. Check the result to safely finish this action.";
			}
			return false;
		} finally {
			if (version === generation) {
				pending.value = false;
				activeRequest.value = null;
			}
		}
	}

	function run(
		action: SessionAction,
		execute: PendingRequest["execute"],
		details: Pick<PendingRequest, "target" | "trackIds"> = {},
	) {
		if (!enabled.value) return Promise.resolve(false);
		return send({ id: crypto.randomUUID(), action, execute, ...details });
	}
	function control(action: PlaybackAction) {
		const body = { action, expected_attempt_id: session.value?.playback.attempt_id ?? null };
		return run(`playback.${action}`, (id) => api.control(id, body));
	}
	function addMany(ids: string[], skipDuplicates = false) {
		const trackIds = [...ids];
		return run(
			"queue.added",
			(id) => api.addTracks(id, { track_ids: trackIds, skip_duplicates: skipDuplicates }),
			{ trackIds },
		);
	}
	async function add(sourceUrl: string) {
		if (!enabled.value) return false;
		const version = generation;
		resolving.value = sourceUrl;
		error.value = null;
		try {
			const track = await catalog.resolveTrack(sourceUrl);
			if (version !== generation) return false;
			resolving.value = null;
			return await addMany([track.id]);
		} catch (reason) {
			if (version === generation && !(reason instanceof SessionLost))
				error.value =
					reason instanceof Error ? reason.message : "Could not resolve this track.";
			return false;
		} finally {
			if (version === generation) resolving.value = null;
		}
	}
	function isPending(action: SessionAction, target?: string) {
		return (
			activeRequest.value?.action === action &&
			(target === undefined || activeRequest.value.target === target)
		);
	}
	function dispose() {
		generation++;
		disconnect?.();
		disconnect = undefined;
		connection.value = "offline";
		session.value = null;
		channels.value = [];
		channelsLoading.value = channelError.value = pending.value = false;
		uncertain.value = activeRequest.value = null;
		activity.value = null;
		error.value = resolving.value = null;
		settled.clear();
	}

	onScopeDispose(dispose);
	return {
		session,
		positionUpdatedAt,
		snapshot,
		connection,
		channels,
		channelError,
		channelsLoading,
		pending,
		activeRequest,
		activity,
		resolving,
		uncertain,
		error,
		enabled,
		connect,
		dispose,
		refreshChannels,
		control,
		add,
		addMany,
		isPending,
		isAdding: (id: string) =>
			resolving.value === id ||
			(isPending("queue.added") && !!activeRequest.value?.trackIds?.includes(id)),
		isControlPending: (action: PlaybackAction | "leave") =>
			isPending(action === "leave" ? "connection.leave" : `playback.${action}`),
		retry: () =>
			uncertain.value && !pending.value && connection.value === "live"
				? send(uncertain.value)
				: Promise.resolve(false),
		removeTrack: (entryId: string) =>
			run("queue.removed", (id) => api.removeTrack(id, entryId), { target: entryId }),
		move: (entryId: string, beforeId: string | null, revision: number) =>
			run(
				"queue.reordered",
				(id) =>
					api.moveTrack(id, entryId, {
						before_entry_id: beforeId,
						expected_queue_revision: revision,
					}),
				{ target: entryId },
			),
		clearQueue: (revision: number, contributorId: string | null = null) =>
			run("queue.cleared", (id) =>
				api.clearQueue(id, {
					expected_queue_revision: revision,
					contributor_id: contributorId,
				}),
			),
		undo: (undoId: string) =>
			run("queue.restored", (id) => api.undo(id, undoId), { target: undoId }),
		seek: (seconds: number, attempt: string) =>
			run("playback.seek", (id) => api.seek(id, { seconds, expected_attempt_id: attempt })),
		setVolume: (volume: number) => run("playback.volume", (id) => api.setVolume(id, volume)),
		setCrossfade: (seconds: Parameters<typeof api.setCrossfade>[1]) =>
			run("playback.crossfade", (id) => api.setCrossfade(id, seconds)),
		join: (channelId: string) => run("connection.join", (id) => api.join(id, channelId)),
		leave: () => run("connection.leave", (id) => api.control(id, { action: "leave" })),
		startRadio: (seed: MediaReference, expectedGeneration: string | null) =>
			run("radio.started", (id) =>
				api.startRadio(id, { seed, expected_generation: expectedGeneration }),
			),
		stopRadio: (radio: string) =>
			run("radio.stopped", (id) => api.stopRadio(id, radio), { target: radio }),
		retryRadio: (radio: string) =>
			run("radio.retried", (id) => api.retryRadio(id, radio), { target: radio }),
	};
});

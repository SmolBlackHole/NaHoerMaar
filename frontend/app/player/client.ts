import { computed, ref, shallowRef } from "vue";
import {
	presentSession,
	presentTrack,
	type Track,
	type SessionState,
	type Outcome,
	type MutationReply,
} from "../../shared/engine";
import type {
	MutationResult,
	PlaybackAction,
	PlayerState,
	VoiceChannel,
} from "../../shared/player";

export interface PendingRequest {
	id: string;
	path: string;
	method: "POST" | "PUT" | "DELETE";
	body?: string;
}

const messages: Record<string, string> = {
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

export function createPlayerClient(
	request: typeof fetch = (...args) => fetch(...args),
	openEvents: (url: string) => EventSource = (url) => new EventSource(url),
	auth?: {
		lost: (code: string) => void;
		check: () => Promise<void>;
		userId?: () => string | undefined;
	},
) {
	const snapshot = shallowRef<PlayerState | null>(null);
	const connection = ref<"connecting" | "live" | "offline">("connecting");
	const channels = shallowRef<VoiceChannel[]>([]);
	const channelError = ref(false);
	const channelsLoading = ref(false);
	const pending = ref(false);
	const activeRequest = shallowRef<PendingRequest | null>(null);
	const completed = shallowRef<{ request: PendingRequest; result: MutationResult } | null>(null);
	const activity = shallowRef<{
		id: string;
		action: string;
		result: MutationResult;
		own: boolean;
	} | null>(null);
	const resolving = ref<string | null>(null);
	const uncertain = shallowRef<PendingRequest | null>(null);
	const error = ref<string | null>(null);
	const enabled = computed(
		() =>
			connection.value === "live" &&
			!!snapshot.value &&
			!snapshot.value.last_issue?.fatal &&
			!pending.value &&
			!resolving.value &&
			!uncertain.value,
	);
	let events: EventSource | undefined;
	let disposed = false;
	let generation = 0;
	let tracks: Record<string, Track> = {};
	function presentOutcome(outcome: Outcome): MutationResult {
		return {
			...outcome,
			replayed: false,
			entries: outcome.entries.map((item) => presentTrack(tracks[item.track_id], item)),
		};
	}

	function accept(state: SessionState) {
		if (!Number.isSafeInteger(state.session?.revision) || !Array.isArray(state.queue))
			throw new Error("Invalid session state");
		if (
			snapshot.value?.session_id === state.session.id &&
			state.session.revision < snapshot.value.revision
		)
			return false;
		if (snapshot.value?.session_id !== state.session.id) tracks = {};
		tracks = { ...tracks, ...state.tracks };
		snapshot.value = presentSession(
			state,
			snapshot.value?.session_id === state.session.id ? snapshot.value : null,
		);
		return true;
	}

	async function refreshChannels() {
		if (channelsLoading.value) return;
		channelsLoading.value = true;
		const version = generation;
		try {
			const response = await request("/api/channels", {
				signal: AbortSignal.timeout(15_000),
			});
			if (!response.ok) throw new Error("channels unavailable");
			const values = (await response.json()) as VoiceChannel[];
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
		disposed = false;
		events?.close();
		connection.value = "connecting";
		const stream = openEvents("/api/events");
		events = stream;
		stream.onopen = () => {
			if (events === stream && !disposed) connection.value = "connecting";
		};
		const receive = (event: Event, change: boolean) => {
			if (events !== stream || disposed) return;
			try {
				const data = JSON.parse((event as MessageEvent).data);
				const state: SessionState = change ? data.state : data;
				if (!accept(state)) return;
				if (change)
					activity.value = {
						id: `${state.session.id}:${state.session.revision}`,
						action: data.action,
						result: presentOutcome(data.outcome),
						own: !!data.outcome.actor && data.outcome.actor.id === auth?.userId?.(),
					};
				const reconnected = connection.value !== "live";
				connection.value = "live";
				if (reconnected) void refreshChannels();
			} catch {
				connection.value = "offline";
			}
		};
		stream.addEventListener("state", (event) => receive(event, false));
		stream.addEventListener("change", (event) => receive(event, true));
		stream.onerror = () => {
			if (events === stream && !disposed) {
				connection.value = "offline";
				void auth?.check();
			}
		};
		stream.addEventListener("auth", (event) => {
			if (events !== stream || disposed) return;
			dispose();
			let code = "signed_out";
			try {
				code = JSON.parse((event as MessageEvent).data).code;
			} catch {
				/* Close on malformed auth events too. */
			}
			auth?.lost(code);
		});
	}

	async function send(command: PendingRequest): Promise<boolean> {
		const version = generation;
		pending.value = true;
		activeRequest.value = command;
		error.value = null;
		try {
			const response = await request(command.path, {
				method: command.method,
				headers: { "Content-Type": "application/json", "Idempotency-Key": command.id },
				body: command.body,
				signal: AbortSignal.timeout(30_000),
			});
			const result = (await response.json()) as Partial<MutationReply> & { code?: string };
			if (version !== generation) return false;
			if (result.state) accept(result.state);
			if (response.status >= 500 && !result.state) throw new Error("unknown outcome");
			uncertain.value = null;
			if (!response.ok || result.outcome?.code !== "ok") {
				error.value =
					response.status === 422
						? "The request is invalid. Check your selection and try again."
						: (messages[result.outcome?.code ?? result.code ?? ""] ??
							"The action was rejected. Refresh the connection and try again.");
				return false;
			}
			if (!result.state || !result.outcome) throw new Error("unknown outcome");
			completed.value = {
				request: command,
				result: {
					...result.outcome,
					replayed: !!result.replayed,
					entries: result.outcome.entries.map((item) =>
						presentTrack(tracks[item.track_id], item),
					),
				},
			};
			return true;
		} catch {
			if (version !== generation) return false;
			uncertain.value = command;
			error.value = "The response was lost. Check the result to safely finish this action.";
			return false;
		} finally {
			if (version === generation) {
				pending.value = false;
				activeRequest.value = null;
			}
		}
	}

	function mutate(path: string, method: PendingRequest["method"], body?: object) {
		if (!enabled.value) return Promise.resolve(false);
		return send({
			id: crypto.randomUUID(),
			path,
			method,
			body: body ? JSON.stringify(body) : undefined,
		});
	}

	function control(action: PlaybackAction) {
		return mutate("/api/playback/control", "POST", {
			action,
			expected_attempt_id: snapshot.value?.attempt_id ?? null,
		});
	}

	function seek(positionSeconds: number, attemptId: string) {
		return mutate("/api/playback/position", "PUT", {
			seconds: positionSeconds,
			expected_attempt_id: attemptId,
		});
	}

	function move(entryId: string, beforeId: string | null, revision: number) {
		return mutate(`/api/queue/${entryId}/position`, "PUT", {
			before_entry_id: beforeId,
			expected_queue_revision: revision,
		});
	}

	function retry() {
		if (!uncertain.value || pending.value || connection.value !== "live")
			return Promise.resolve(false);
		return send(uncertain.value);
	}

	function isPending(path: string, method?: PendingRequest["method"]) {
		return (
			activeRequest.value?.path === path && (!method || activeRequest.value.method === method)
		);
	}

	function isAdding(trackId: string) {
		return (
			resolving.value === trackId ||
			(isPending("/api/queue") &&
				!!JSON.parse(activeRequest.value?.body ?? "{}").track_ids?.includes(trackId))
		);
	}
	function isControlPending(action: PlaybackAction | "leave") {
		return (
			isPending("/api/playback/control") &&
			JSON.parse(activeRequest.value?.body ?? "{}").action === action
		);
	}
	async function add(sourceUrl: string) {
		if (!enabled.value) return false;
		const version = generation;
		resolving.value = sourceUrl;
		error.value = null;
		try {
			const response = await request("/api/catalog/track", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ source_url: sourceUrl }),
				signal: AbortSignal.timeout(35_000),
			});
			if (!response.ok) throw new Error("Could not resolve this track. Try again.");
			const track: Track = await response.json();
			if (version !== generation) return false;
			tracks[track.id] = track;
			resolving.value = null;
			return await addMany([track.id]);
		} catch (reason) {
			if (version === generation)
				error.value =
					reason instanceof Error ? reason.message : "Could not resolve this track.";
			return false;
		} finally {
			if (version === generation) resolving.value = null;
		}
	}
	function addMany(trackIds: string[], skipDuplicates = false) {
		return mutate("/api/queue", "POST", {
			track_ids: trackIds,
			skip_duplicates: skipDuplicates,
		});
	}

	function dispose() {
		generation++;
		disposed = true;
		events?.close();
		events = undefined;
		connection.value = "offline";
		snapshot.value = null;
		channels.value = [];
		channelsLoading.value = false;
		channelError.value = false;
		uncertain.value = null;
		error.value = null;
		pending.value = false;
		activeRequest.value = null;
		completed.value = null;
		activity.value = null;
		resolving.value = null;
		tracks = {};
	}

	return {
		activity,
		resolving,
		add,
		addMany,
		isControlPending,
		snapshot,
		connection,
		channels,
		channelError,
		channelsLoading,
		pending,
		activeRequest,
		completed,
		isPending,
		isAdding,
		uncertain,
		error,
		enabled,
		connect,
		dispose,
		refreshChannels,
		mutate,
		control,
		seek,
		move,
		retry,
	};
}

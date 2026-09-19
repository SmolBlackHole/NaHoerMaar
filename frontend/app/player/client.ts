import { computed, ref, shallowRef } from "vue";
import type {
	MutationResult,
	PlaybackAction,
	PlayerState,
	VoiceChannel,
} from "../../shared/player";

interface PendingRequest {
	id: string;
	path: string;
	method: "POST" | "PUT" | "DELETE";
	body?: string;
}

const messages: Record<string, string> = {
	queue_conflict: "The queue changed. The latest order is shown; move the track again if needed.",
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
) {
	const snapshot = shallowRef<PlayerState | null>(null);
	const connection = ref<"connecting" | "live" | "offline">("connecting");
	const channels = shallowRef<VoiceChannel[]>([]);
	const channelError = ref(false);
	const channelsLoading = ref(false);
	const pending = ref(false);
	const uncertain = shallowRef<PendingRequest | null>(null);
	const error = ref<string | null>(null);
	const enabled = computed(
		() =>
			connection.value === "live" &&
			!!snapshot.value &&
			!snapshot.value.last_issue?.fatal &&
			!pending.value &&
			!uncertain.value,
	);
	let events: EventSource | undefined;
	let disposed = false;

	function accept(state: PlayerState) {
		if (!snapshot.value || state.revision >= snapshot.value.revision) snapshot.value = state;
	}

	async function refreshChannels() {
		if (channelsLoading.value) return;
		channelsLoading.value = true;
		try {
			const response = await request("/api/channels", {
				signal: AbortSignal.timeout(15_000),
			});
			if (!response.ok) throw new Error("channels unavailable");
			channels.value = (await response.json()) as VoiceChannel[];
			channelError.value = false;
		} catch {
			channelError.value = true;
		} finally {
			channelsLoading.value = false;
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
		stream.addEventListener("state", (event) => {
			if (events !== stream || disposed) return;
			try {
				const state = JSON.parse((event as MessageEvent).data) as PlayerState;
				if (!Number.isSafeInteger(state.revision) || !Array.isArray(state.upcoming))
					throw new Error("invalid state");
				accept(state);
				const reconnected = connection.value !== "live";
				connection.value = "live";
				if (reconnected) void refreshChannels();
			} catch {
				connection.value = "offline";
			}
		});
		stream.onerror = () => {
			if (events === stream && !disposed) connection.value = "offline";
		};
	}

	async function send(command: PendingRequest): Promise<boolean> {
		pending.value = true;
		error.value = null;
		try {
			const response = await request(command.path, {
				method: command.method,
				headers: { "Content-Type": "application/json", "Idempotency-Key": command.id },
				body: command.body,
				signal: AbortSignal.timeout(30_000),
			});
			const result = (await response.json()) as Partial<MutationResult>;
			if (result.snapshot) accept(result.snapshot);
			if (response.status >= 500 && !result.snapshot) throw new Error("unknown outcome");
			uncertain.value = null;
			if (!response.ok || result.code !== "ok") {
				error.value =
					response.status === 422
						? "Use a link to a single YouTube video. Search and playlists aren't available yet."
						: (messages[result.code ?? ""] ??
							"The action was rejected. Refresh the connection and try again.");
				return false;
			}
			return true;
		} catch {
			uncertain.value = command;
			error.value = "The response was lost. Check the result to safely finish this action.";
			return false;
		} finally {
			pending.value = false;
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
		return mutate(`/api/player/${action}`, "POST", {
			expected_playback_id: snapshot.value?.playback_id ?? null,
		});
	}

	function seek(positionSeconds: number, playbackId: string) {
		return mutate("/api/player/seek", "PUT", {
			position_seconds: positionSeconds,
			expected_playback_id: playbackId,
		});
	}

	function move(entryId: string, beforeId: string | null, revision: number) {
		return mutate(`/api/queue/${entryId}/move`, "POST", {
			before_entry_id: beforeId,
			expected_queue_revision: revision,
		});
	}

	function retry() {
		if (!uncertain.value || pending.value || connection.value !== "live")
			return Promise.resolve(false);
		return send(uncertain.value);
	}

	function dispose() {
		disposed = true;
		events?.close();
		events = undefined;
		connection.value = "offline";
	}

	return {
		snapshot,
		connection,
		channels,
		channelError,
		channelsLoading,
		pending,
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

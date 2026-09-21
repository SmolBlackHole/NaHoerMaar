import type { components } from "../../shared/api.generated";
import type { MutationReply, SessionState, SessionChange, VoiceChannel } from "../../shared/engine";
import type { HttpTransport } from "./transport";

type Schema = components["schemas"];
export type SessionEvent =
	| { type: "state"; data: SessionState }
	| { type: "change"; data: SessionChange }
	| { type: "auth"; code: string }
	| { type: "connecting" | "offline" };

export function createSessionRepository(
	json: HttpTransport,
	openEvents: (url: string) => EventSource = (url) => new EventSource(url),
) {
	function command(path: string, id: string, method: string, body?: object) {
		return json<MutationReply>(path, {
			method,
			headers: { "Content-Type": "application/json", "Idempotency-Key": id },
			body: body === undefined ? undefined : JSON.stringify(body),
		});
	}
	return {
		channels: () => json<VoiceChannel[]>("/api/channels"),
		addTracks: (id: string, body: Schema["AddInput"]) =>
			command("/api/queue", id, "POST", body),
		removeTrack: (id: string, entryId: string) =>
			command(`/api/queue/${entryId}`, id, "DELETE"),
		moveTrack: (id: string, entryId: string, body: Schema["MoveInput"]) =>
			command(`/api/queue/${entryId}/position`, id, "PUT", body),
		clearQueue: (id: string, body: Schema["ClearInput"]) =>
			command("/api/queue/clear", id, "POST", body),
		undo: (id: string, undoId: string) => command(`/api/queue/undo/${undoId}`, id, "POST"),
		control: (id: string, body: Schema["ControlInput"]) =>
			command("/api/playback/control", id, "POST", body),
		seek: (id: string, body: Schema["SeekInput"]) =>
			command("/api/playback/position", id, "PUT", body),
		setVolume: (id: string, volume: number) =>
			command("/api/playback/volume", id, "PUT", { volume } satisfies Schema["VolumeInput"]),
		setCrossfade: (id: string, seconds: Schema["CrossfadeInput"]["seconds"]) =>
			command("/api/playback/crossfade", id, "PUT", {
				seconds,
			} satisfies Schema["CrossfadeInput"]),
		join: (id: string, channelId: string) =>
			command("/api/connection", id, "PUT", {
				channel_id: channelId,
			} satisfies Schema["JoinInput"]),
		startRadio: (id: string, body: Schema["RadioInput"]) =>
			command("/api/radio", id, "POST", body),
		stopRadio: (id: string, generation: string) =>
			command(`/api/radio/${generation}/stop`, id, "POST"),
		retryRadio: (id: string, generation: string) =>
			command(`/api/radio/${generation}/retry`, id, "POST"),

		subscribe(receive: (event: SessionEvent) => void) {
			const stream = openEvents("/api/events");
			let closed = false;
			const close = () => {
				if (!closed) {
					closed = true;
					stream.close();
				}
			};
			const emit = (event: SessionEvent) => {
				if (!closed) receive(event);
			};
			stream.onopen = () => emit({ type: "connecting" });
			stream.onerror = () => emit({ type: "offline" });
			for (const type of ["state", "change"] as const) {
				stream.addEventListener(type, (event) => {
					if (closed) return;
					try {
						emit({ type, data: JSON.parse((event as MessageEvent).data) });
					} catch {
						emit({ type: "offline" });
					}
				});
			}
			stream.addEventListener("auth", (event) => {
				if (closed) return;
				let code = "signed_out";
				try {
					code = JSON.parse((event as MessageEvent).data).code ?? code;
				} catch {}
				close();
				receive({ type: "auth", code });
			});
			return close;
		},
	};
}
export type SessionRepository = ReturnType<typeof createSessionRepository>;

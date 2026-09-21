import {
	presentSession,
	type SessionState,
	type Track,
	type Outcome,
	type DiscoveryPage,
} from "../shared/engine";

export const track: Track = {
	id: "e3e3b6d8-52e1-4f17-b3f0-3aa917c79a99",
	identity: { namespace: "youtube", external_id: "Pqp9fDRp1lw" },
	source_url: "https://www.youtube.com/watch?v=Pqp9fDRp1lw",
	metadata: {
		title: "Амура",
		artist: "Artist",
		uploader: "Uploader",
		uploader_url: null,
		duration_seconds: 180,
		thumbnail_url: null,
	},
};
export const occurrence = {
	id: "c68fe9f1-ac72-4f15-9e7f-445d332b9ca7",
	track_id: track.id,
	position: 0,
	added_by: null,
	origin: "manual" as const,
};
export const outcome = (changes: Partial<Outcome> = {}): Outcome => ({
	code: "ok",
	added_count: 0,
	removed_count: 0,
	restored_count: 0,
	skipped_count: 0,
	entries: [],
	actor: null,
	undo_id: null,
	undo_expires_at: null,
	...changes,
});
export function session(): SessionState {
	return {
		session: {
			id: "session",
			revision: 1,
			queue_revision: 1,
			channel_id: "123456789012345678",
			volume: 1,
			crossfade_seconds: 0,
		},
		queue: [{ ...occurrence }],
		checkpoint: {
			intent: "stopped",
			entry_id: null,
			track_id: null,
			play_id: null,
			position_seconds: 0,
			added_by: null,
			origin: "manual",
		},
		playback: {
			phase: "idle",
			attempt_id: null,
			connection_id: "connection",
			joining_id: null,
			duration_seconds: null,
			error: null,
		},
		history: [],
		radio: { mode: "manual" },
		tracks: { [track.id]: structuredClone(track) },
	};
}
export function playing(): SessionState {
	const value = session();
	value.checkpoint = {
		...value.checkpoint,
		intent: "playing",
		track_id: track.id,
		entry_id: occurrence.id,
		play_id: "play",
	};
	value.playback = { ...value.playback, phase: "playing", attempt_id: "attempt" };
	value.queue = [];
	return value;
}
export function discovery(
	version = "original",
	changes: Partial<DiscoveryPage> = {},
): DiscoveryPage {
	return {
		version,
		offset: 0,
		total: 1,
		error: null,
		entries: [
			{
				position: 0,
				track_id: track.id,
				finding: {
					reference: {
						identity: { ...track.identity },
						source_url: track.source_url,
						kind: "track",
					},
					metadata: { ...track.metadata, title: version },
				},
			},
		],
		refresh: { latest_version: version, refreshing: false, error: null },
		...changes,
	};
}
export const view = () => presentSession(session(), null);

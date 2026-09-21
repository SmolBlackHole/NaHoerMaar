import type { ListenerProfile } from "./profile";
import type { PlayerState, QueueEntry } from "./player";

/** Native engine wire types. Queue occurrences reference catalog tracks by ID. */
export interface MediaReference {
	identity: { namespace: string; external_id: string };
	kind: "track" | "playlist";
	source_url: string;
}
export interface TrackMetadata {
	title: string | null;
	artist: string | null;
	uploader: string | null;
	uploader_url: string | null;
	duration_seconds: number | null;
	thumbnail_url: string | null;
}
export interface Track {
	id: string;
	identity: MediaReference["identity"];
	source_url: string;
	metadata: TrackMetadata;
}
export interface Occurrence {
	id: string;
	track_id: string;
	added_by: ListenerProfile | null;
	origin: "manual" | "radio";
}
export interface Outcome {
	code: string;
	added_count: number;
	removed_count: number;
	restored_count: number;
	skipped_count: number;
	entries: Occurrence[];
	actor: ListenerProfile | null;
	undo_id: string | null;
	undo_expires_at: string | null;
}
export interface SessionState {
	session: {
		id: string;
		revision: number;
		queue_revision: number;
		channel_id: string | null;
		volume: number;
		crossfade_seconds: number;
	};
	queue: (Occurrence & { position: number })[];
	checkpoint: {
		intent: "stopped" | "playing" | "paused";
		entry_id: string | null;
		track_id: string | null;
		play_id: string | null;
		position_seconds: number;
		added_by: ListenerProfile | null;
		origin: "manual" | "radio";
	};
	playback: {
		phase: "idle" | "resolving" | "starting" | "playing" | "paused" | "suspended";
		attempt_id: string | null;
		connection_id: string | null;
		joining_id: string | null;
		duration_seconds: number | null;
		error: string | null;
	};
	history: (Occurrence & {
		entry_id: string;
		started_at: string;
		ended_at: string | null;
		end_reason: "completed" | "skipped" | "stopped" | "failed" | null;
	})[];
	radio:
		| { mode: "manual" }
		| {
				mode: "radio";
				generation: string;
				seed: MediaReference;
				state: "active" | "loading" | "waiting";
				initiator: ListenerProfile | null;
				error: string | null;
		  };
	tracks: Record<string, Track>;
}
export interface MutationReply {
	state: SessionState;
	outcome: Outcome;
	replayed: boolean;
}
export interface SessionChange {
	state: SessionState;
	outcome: Outcome;
	action: string;
}
export interface DiscoveryPage {
	version: string;
	offset: number;
	total: number;
	error: string | null;
	playlist?: MediaReference;
	title?: string | null;
	entries: {
		position: number;
		track_id: string | null;
		finding: { reference: MediaReference | null; metadata: TrackMetadata; reason?: string };
	}[];
	refresh: { latest_version: string; refreshing: boolean; error: string | null };
}

export function presentTrack(track: Track | undefined, occurrence: Occurrence): QueueEntry {
	return {
		title: null,
		artist: null,
		uploader: null,
		uploader_url: null,
		thumbnail_url: null,
		duration_seconds: null,
		...track?.metadata,
		...occurrence,
		source_url: track?.source_url ?? "",
		video_id: track?.identity.namespace === "youtube" ? track.identity.external_id : null,
	};
}

/** A UI projection, never sent back as a session or a mutation payload. */
export function presentSession(value: SessionState, previous: PlayerState | null): PlayerState {
	const { session, checkpoint, playback, tracks } = value;
	const entry = (item: Occurrence) => presentTrack(tracks[item.track_id]!, item);
	const current =
		checkpoint.track_id && checkpoint.entry_id
			? entry({ ...checkpoint, id: checkpoint.entry_id, track_id: checkpoint.track_id })
			: null;
	if (current && playback.duration_seconds !== null)
		current.duration_seconds = playback.duration_seconds;
	const state =
		playback.phase === "playing"
			? "playing"
			: checkpoint.intent === "paused" && current
				? "paused"
				: ["resolving", "starting"].includes(playback.phase)
					? "loading"
					: "idle";
	const samePosition =
		previous?.playback_id === checkpoint.play_id &&
		previous.attempt_id === playback.attempt_id &&
		previous.state === state &&
		previous.position_seconds === checkpoint.position_seconds;
	const radio = value.radio;
	const failed =
		playback.error === "track_failed"
			? [...value.history]
					.filter((item) => item.end_reason === "failed")
					.sort((a, b) => Date.parse(b.ended_at!) - Date.parse(a.ended_at!))[0]
			: undefined;
	return {
		session_id: session.id,
		revision: session.revision,
		queue_revision: session.queue_revision,
		state,
		current,
		upcoming: value.queue.map(entry),
		recently_played: value.history
			.filter((item) => item.ended_at !== null)
			.map((item) => ({
				id: item.id,
				played_at: item.started_at,
				entry: entry({ ...item, id: item.entry_id }),
			})),
		voice_state: playback.joining_id
			? "connecting"
			: playback.connection_id
				? "connected"
				: "disconnected",
		channel_id: session.channel_id,
		playback_id: checkpoint.play_id,
		attempt_id: playback.attempt_id,
		volume: session.volume,
		crossfade_seconds: session.crossfade_seconds,
		position_seconds: checkpoint.position_seconds,
		position_updated_at: samePosition ? previous.position_updated_at : new Date().toISOString(),
		last_issue: playback.error
			? {
					id: failed?.id ?? `${session.id}:${playback.error}`,
					entry_id: failed?.entry_id ?? null,
					entry: failed ? entry({ ...failed, id: failed.entry_id }) : null,
					code: "playback_failed",
					fatal: false,
					reason: failed ? "source_unavailable" : "voice_unavailable",
				}
			: null,
		radio:
			radio.mode === "manual"
				? {
						state: "off",
						generation: null,
						seed: null,
						title: null,
						initiator: null,
						error: null,
					}
				: {
						...radio,
						title:
							Object.values(tracks).find(
								(track) =>
									track.identity.namespace === radio.seed.identity.namespace &&
									track.identity.external_id === radio.seed.identity.external_id,
							)?.metadata.title ??
							(previous?.radio.generation === radio.generation
								? previous.radio.title
								: null) ??
							(radio.seed.kind === "playlist" ? "Playlist radio" : "Track radio"),
					},
	};
}

import type { components } from "./api.generated";
import type { PlayerState, QueueEntry } from "./player";

type Schema = components["schemas"];
export type MediaReference = Schema["MediaReference"];
export type TrackMetadata = Schema["MetadataView"];
export type Track = Schema["TrackView"];
export type Occurrence = Schema["OccurrenceView"];
export type Outcome = Schema["OutcomeView"];
export type SessionState = Schema["SessionView"];
export type MutationReply = Schema["MutationView"];
export type SessionChange = Schema["ChangeView"];
export type DiscoveryPage = Schema["DiscoveryView"];
export type CatalogEntry = Schema["CatalogEntryView"];
export type SessionAction = MutationReply["action"];
export type ApiError = Schema["ApiError"];
export type VoiceChannel = Schema["ChannelView"];

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
		reference: track
			? { kind: "track", identity: track.identity, source_url: track.source_url }
			: null,
		source_url: track?.source_url ?? "",
		video_id: track?.identity.namespace === "youtube" ? track.identity.external_id : null,
	};
}

/** A UI projection, never sent back as a session or a mutation payload. */
export function presentSession(
	value: SessionState,
	positionUpdatedAt = new Date().toISOString(),
): PlayerState {
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
		recently_played: value.history.map((item) => ({
				id: item.id,
				played_at: item.started_at,
				entry: entry({ ...item, id: item.entry_id }),
			})),
		voice_state: playback.connection,
		channel_id: session.channel_id,
		playback_id: checkpoint.play_id,
		attempt_id: playback.attempt_id,
		volume: session.volume,
		crossfade_seconds: session.crossfade_seconds,
		position_seconds: checkpoint.position_seconds,
		position_updated_at: positionUpdatedAt,
		last_issue: playback.error
			? {
					id: failed?.id ?? `${session.id}:${playback.error}`,
					entry_id: failed?.entry_id ?? null,
					entry: failed ? entry({ ...failed, id: failed.entry_id }) : null,
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
							(radio.seed.kind === "playlist" ? "Playlist radio" : "Track radio"),
					},
	};
}

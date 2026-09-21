import type { ListenerProfile } from "./profile";
import type { RadioStatus } from "./radio";
import type { Outcome, TrackMetadata, MediaReference, VoiceChannel as Channel } from "./engine";

export type PlaybackState = "idle" | "loading" | "playing" | "paused";
export type PlaybackAction = "play" | "pause" | "skip" | "stop";

export interface TrackDisplay extends TrackMetadata {
	track_id: string | null;
	reference: MediaReference | null;
	source_url: string | null;
	video_id: string | null;
}
export interface QueueEntry extends TrackDisplay {
	id: string;
	track_id: string;
	source_url: string;
	added_by: ListenerProfile | null;
	origin: "manual" | "radio";
}

export interface HistoryEntry {
	id: string;
	played_at: string;
	entry: QueueEntry;
}

export interface RecentTrack extends HistoryEntry {
	play_count: number;
}

export function groupHistory(history: readonly HistoryEntry[]): RecentTrack[] {
	const tracks = new Map<string, RecentTrack>();
	const latestFirst = [...history].sort(
		(a, b) => Date.parse(b.played_at) - Date.parse(a.played_at),
	);
	for (const item of latestFirst) {
		const key = item.entry.track_id;
		const existing = tracks.get(key);
		if (existing) existing.play_count++;
		else tracks.set(key, { ...item, play_count: 1 });
	}
	return [...tracks.values()];
}

export interface PlayerState {
	session_id: string;
	attempt_id: string | null;
	radio: RadioStatus;
	revision: number;
	queue_revision: number;
	state: PlaybackState;
	current: QueueEntry | null;
	upcoming: QueueEntry[];
	recently_played: HistoryEntry[];
	voice_state: "disconnected" | "connecting" | "connected";
	channel_id: string | null;
	playback_id: string | null;
	volume: number;
	crossfade_seconds: number;
	position_seconds: number;
	position_updated_at: string | null;
	last_issue: {
		id: string;
		entry_id: string | null;
		entry: QueueEntry | null;
		reason: "source_unavailable" | "voice_unavailable";
	} | null;
}

export type VoiceChannel = Channel;

export type MutationResult = Omit<Outcome, "entries"> & {
	replayed: boolean;
	entries: QueueEntry[];
};

export function youtubeVideoId(source: string): string | null {
	if (source.length > 2048) return null;
	try {
		const url = new URL(source.trim());
		if (
			!["http:", "https:"].includes(url.protocol) ||
			url.username ||
			url.password ||
			(url.port && !["80", "443"].includes(url.port))
		)
			return null;
		const parts = url.pathname.replace(/^\/+|\/+$/g, "").split("/");
		let id: string | undefined;
		if (url.hostname === "youtu.be" && parts.length === 1) id = parts[0];
		if (
			["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(
				url.hostname,
			)
		) {
			if (
				parts.length === 1 &&
				parts[0] === "watch" &&
				url.searchParams.getAll("v").length === 1
			)
				id = url.searchParams.get("v") ?? undefined;
			if (parts.length === 2 && ["shorts", "embed"].includes(parts[0]!)) id = parts[1];
		}
		return id && /^[\w-]{11}$/.test(id) ? id : null;
	} catch {
		return null;
	}
}

export function trackTitle(entry: TrackDisplay): string {
	return (
		entry.title ||
		`YouTube · ${entry.video_id || youtubeVideoId(entry.source_url ?? "") || "Untitled track"}`
	);
}

export function trackArtist(entry: TrackDisplay): string {
	return entry.artist || entry.uploader || "YouTube";
}

export function trackArtistUrl(entry: TrackDisplay): string | null {
	const sameArtist =
		!entry.artist ||
		entry.artist.toLowerCase() === entry.uploader?.replace(/ - Topic$/i, "").toLowerCase();
	if (entry.uploader_url && sameArtist) {
		try {
			const url = new URL(entry.uploader_url);
			if (
				url.protocol === "https:" &&
				["youtube.com", "www.youtube.com", "music.youtube.com"].includes(url.hostname) &&
				!url.username &&
				!url.password
			)
				return url.href;
		} catch {
			/* Fall back to artist search. */
		}
	}
	return entry.artist || entry.uploader
		? `https://music.youtube.com/search?q=${encodeURIComponent(entry.artist || entry.uploader!)}`
		: null;
}

export function trackArtwork(entry: TrackDisplay | null): string | null {
	if (!entry) return null;
	if (entry.thumbnail_url) {
		try {
			const url = new URL(entry.thumbnail_url);
			if (url.protocol === "https:" && !url.username && !url.password) return url.href;
		} catch {
			/* Use the public YouTube thumbnail. */
		}
	}
	const id = youtubeVideoId(entry.source_url ?? "");
	return id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : null;
}

export function listeningStats(history: HistoryEntry[], days: number, now = new Date()) {
	const buckets = Array.from({ length: days }, (_, index) => {
		const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + index + 1);
		return { date, count: 0 };
	});
	const start = buckets[0]!.date.getTime();
	const entries = history.filter((item) => {
		const time = Date.parse(item.played_at);
		return time >= start && time <= now.getTime();
	});
	for (const item of entries) {
		const date = new Date(item.played_at).toDateString();
		const bucket = buckets.find((day) => day.date.toDateString() === date);
		if (bucket) bucket.count++;
	}
	const artists = new Map<string, number>();
	for (const { entry } of entries) {
		const name = entry.artist || entry.uploader;
		if (name) artists.set(name, (artists.get(name) ?? 0) + 1);
	}
	return {
		starts: entries.length,
		tracks: new Set(entries.map((item) => item.entry.track_id)).size,
		artists: artists.size,
		buckets,
		topArtists: [...artists].sort((a, b) => b[1] - a[1]).slice(0, 5),
	};
}

export function formatTime(seconds: number | null): string {
	if (seconds === null) return "--:--";
	const total = Math.max(0, Math.floor(seconds));
	const minutes = Math.floor(total / 60);
	return minutes >= 60
		? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`
		: `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

export function playbackPosition(state: PlayerState, now: number): number {
	let position = state.position_seconds;
	if (state.state === "playing" && state.position_updated_at)
		position += Math.max(0, (now - Date.parse(state.position_updated_at)) / 1000);
	return Math.max(0, Math.min(position, state.current?.duration_seconds ?? Infinity));
}

export function canControl(state: PlayerState | null, action: PlaybackAction): boolean {
	if (!state) return false;
	if (action === "stop" || action === "skip")
		return state.current !== null && state.attempt_id !== null;
	if (action === "pause") return state.state === "playing";
	return (
		state.voice_state === "connected" &&
		(state.state === "paused" ||
			(state.state === "idle" && (state.upcoming.length > 0 || state.current !== null)))
	);
}

export function queueWaits(state: PlayerState | null, now: number): (number | null)[] {
	if (!state) return [];
	let seconds: number | null =
		state.state === "playing" &&
		state.voice_state === "connected" &&
		state.current?.duration_seconds != null
			? Math.max(0, state.current.duration_seconds - playbackPosition(state, now))
			: null;
	let previousDuration = state.current?.duration_seconds ?? null;
	return state.upcoming.map((entry) => {
		if (seconds !== null) {
			seconds = Math.max(
				0,
				seconds -
					crossfadeDuration(
						state.crossfade_seconds,
						previousDuration,
						entry.duration_seconds,
					),
			);
		}
		const wait = seconds;
		seconds =
			seconds !== null && entry.duration_seconds !== null
				? seconds + entry.duration_seconds
				: null;
		previousDuration = entry.duration_seconds;
		return wait;
	});
}

export function crossfadeDuration(
	setting: number,
	outgoing: number | null,
	incoming: number | null,
): number {
	if (!setting || outgoing === null || incoming === null) return 0;
	if (!Number.isFinite(outgoing) || !Number.isFinite(incoming)) return 0;
	return Math.max(0, Math.min(setting, outgoing / 2, incoming / 2));
}

export function formatWait(seconds: number): string {
	if (seconds < 60) return "In <1 min";
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `In ~${minutes} min`;
	return `In ~${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")} h`;
}

export function queueMoveTarget(
	ids: readonly string[],
	entryId: string,
	position: number,
): string | null | undefined {
	if (
		!Number.isInteger(position) ||
		position < 1 ||
		position > ids.length ||
		!ids.includes(entryId)
	)
		return undefined;
	return ids.filter((id) => id !== entryId)[position - 1] ?? null;
}

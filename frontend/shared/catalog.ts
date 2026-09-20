import { youtubeVideoId, type QueueEntry, type PlayerState } from "./player";

export function queuePresence(sourceUrl: string | null, state: PlayerState | null): string | null {
	const id = sourceUrl && youtubeVideoId(sourceUrl);
	if (!id || !state) return null;
	if (state.current && youtubeVideoId(state.current.source_url) === id) return "Now playing";
	return state.upcoming.some((entry) => youtubeVideoId(entry.source_url) === id)
		? "Already queued"
		: null;
}

export function importCounts(
	urls: readonly string[],
	state: PlayerState | null,
	skipDuplicates: boolean,
) {
	if (!skipDuplicates) return { added: urls.length, skipped: 0 };
	const seen = new Set<string>();
	let added = 0;
	for (const url of urls) {
		const id = youtubeVideoId(url) ?? url;
		if (!seen.has(id) && !queuePresence(url, state)) added++;
		seen.add(id);
	}
	return { added, skipped: urls.length - added };
}

export type SearchSource = "youtube_music" | "youtube";

export interface CatalogTrack extends Omit<QueueEntry, "id" | "source_url" | "added_by"> {
	index: number;
	source_url: string | null;
	unavailable: string | null;
}

export interface PlaylistPreview {
	id: string;
	source_url: string;
	state: "loading" | "ready" | "cancelled" | "failed";
	title: string | null;
	entries: CatalogTrack[];
	limit: number;
	truncated: boolean;
	error: string | null;
	snapshot_id: string | null;
	refreshing: boolean;
	refresh_error: string | null;
}

export interface SearchPage {
	entries: CatalogTrack[];
	next_offset: number | null;
	snapshot_id: string;
	latest_snapshot_id: string;
	refreshing: boolean;
	refresh_error: string | null;
}

export type MusicSource =
	| { kind: "video"; url: string; playlist: string | null }
	| { kind: "playlist"; url: string }
	| { kind: "search"; query: string }
	| { kind: "invalid" };

export function musicSource(input: string): MusicSource {
	const text = input.trim();
	if (!text || text.length > 2048) return { kind: "invalid" };
	const link = /^(?:(?:www|music|m)\.)?(?:youtube\.com|youtu\.be)\//i.test(text)
		? `https://${text}`
		: text;
	try {
		const url = new URL(link);
		if (
			!["http:", "https:"].includes(url.protocol) ||
			url.username ||
			url.password ||
			(url.port && !["80", "443"].includes(url.port)) ||
			![
				"youtube.com",
				"www.youtube.com",
				"music.youtube.com",
				"m.youtube.com",
				"youtu.be",
			].includes(url.hostname)
		)
			return { kind: "invalid" };
		const video = youtubeVideoId(url.href);
		const lists = url.searchParams.getAll("list");
		const playlist =
			lists.length === 1 &&
			/^[\w-]{10,150}$/.test(lists[0]!) &&
			(url.pathname.replace(/\/$/, "") === "/playlist" || video)
				? `https://www.youtube.com/playlist?list=${lists[0]}`
				: null;
		if (video)
			return { kind: "video", url: `https://www.youtube.com/watch?v=${video}`, playlist };
		if (playlist) return { kind: "playlist", url: playlist };
		return { kind: "invalid" };
	} catch {
		if (/^[a-z][a-z\d+.-]*:/i.test(text) || /^www\./i.test(text) || text.length > 200)
			return { kind: "invalid" };
		return { kind: "search", query: text };
	}
}

export function catalogEntry(track: CatalogTrack): QueueEntry {
	return {
		...track,
		id: `catalog-${track.index}`,
		source_url: track.source_url ?? "",
		added_by: null,
	};
}

export function selectedSources(
	entries: readonly CatalogTrack[],
	selected: ReadonlySet<number>,
): string[] {
	return entries
		.filter((item) => selected.has(item.index) && item.source_url && !item.unavailable)
		.map((item) => item.source_url!);
}

/** Retain selections only where a refreshed occurrence can be identified safely. */
export function reconcileSelection(
	previous: readonly CatalogTrack[],
	next: readonly CatalogTrack[],
	selected: ReadonlySet<number>,
): Set<number> {
	const groups = (entries: readonly CatalogTrack[]) => {
		const result = new Map<string, CatalogTrack[]>();
		for (const item of entries) {
			const key = item.video_id || item.source_url;
			if (key) result.set(key, [...(result.get(key) ?? []), item]);
		}
		return result;
	};
	const before = groups(previous),
		after = groups(next);
	const kept = new Set<number>();
	for (const [key, entries] of after) {
		const old = before.get(key);
		if (!old) continue;
		// Identical duplicates cannot be matched individually after an upstream edit.
		const allSelected = old.every((item) => selected.has(item.index));
		if (old.length !== entries.length || (old.length > 1 && !allSelected)) continue;
		for (const item of entries) {
			if (allSelected && item.source_url && !item.unavailable) kept.add(item.index);
		}
	}
	return kept;
}

import { youtubeVideoId, type QueueEntry } from "./player";

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
}

export interface SearchPage {
	entries: CatalogTrack[];
	next_offset: number | null;
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

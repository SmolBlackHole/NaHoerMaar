import { youtubeVideoId, type TrackDisplay, type PlayerState } from "./player";
import type { DiscoveryPage } from "./engine";

export function queuePresence(trackId: string | null, state: PlayerState | null): string | null {
	if (!trackId || !state) return null;
	if (state.current?.track_id === trackId) return "Now playing";
	return state.upcoming.some((entry) => entry.track_id === trackId) ? "Already queued" : null;
}
export function importCounts(
	trackIds: readonly string[],
	state: PlayerState | null,
	skipDuplicates: boolean,
) {
	if (!skipDuplicates) return { added: trackIds.length, skipped: 0 };
	const seen = new Set<string>();
	let added = 0;
	for (const id of trackIds) {
		if (!seen.has(id) && !queuePresence(id, state)) added++;
		seen.add(id);
	}
	return { added, skipped: trackIds.length - added };
}
export type SearchSource = "youtube_music" | "youtube";
export interface CatalogTrack extends TrackDisplay {
	index: number;
	unavailable: string | null;
}
export type CatalogPage = Omit<DiscoveryPage, "entries"> & { entries: CatalogTrack[] };
export function catalogPage(page: DiscoveryPage): CatalogPage {
	return {
		...page,
		entries: page.entries.map(({ position, track_id, reference, metadata, unavailable }) => ({
			...metadata,
			track_id,
			reference,
			index: position,
			source_url: reference?.source_url ?? null,
			video_id:
				reference?.identity.namespace === "youtube" ? reference.identity.external_id : null,
			unavailable: unavailable ?? (!track_id ? "Track unavailable" : null),
		})),
	};
}
export function selectedTrackIds(
	entries: readonly CatalogTrack[],
	selected: ReadonlySet<number>,
): string[] {
	return entries
		.filter((item) => selected.has(item.index) && item.track_id && !item.unavailable)
		.map((item) => item.track_id!);
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

/** Retain selections only where a refreshed occurrence can be identified safely. */
export function reconcileSelection(
	previous: readonly CatalogTrack[],
	next: readonly CatalogTrack[],
	selected: ReadonlySet<number>,
): Set<number> {
	const groups = (entries: readonly CatalogTrack[]) => {
		const result = new Map<string, CatalogTrack[]>();
		for (const item of entries) {
			const key = item.track_id;
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
			if (allSelected && item.track_id && !item.unavailable) kept.add(item.index);
		}
	}
	return kept;
}

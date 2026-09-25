// SPDX-FileCopyrightText: 2026 SmolBlackHole
// SPDX-License-Identifier: MPL-2.0

import type { components } from "../api/schema.generated";

type Schema = components["schemas"];

export type Discovery = Schema["DiscoveryView"];
export type DiscoveryKind = Schema["DiscoveryKind"];
export type Track = Schema["TrackView"];
export type DiscoveryEntry = Schema["DiscoveryEntryView"];
export type SearchProvider = "youtube_music" | "youtube";

export type MusicSource =
	| { kind: "video"; url: string; playlist: string | null }
	| { kind: "playlist"; url: string }
	| { kind: "search"; query: string }
	| { kind: "invalid" };

function youtubeVideoId(url: URL): string | null {
	const id = url.hostname === "youtu.be" ? url.pathname.slice(1) : url.searchParams.get("v");
	return id && /^[\w-]{11}$/.test(id) ? id : null;
}

export function musicSource(input: string): MusicSource {
	const text = input.trim();
	if (!text || text.length > 2048) return { kind: "invalid" };
	const link = /^(?:(?:www|music|m)\.)?(?:youtube\.com|youtu\.be)\//i.test(text)
		? "https://" + text
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
		const videoId = youtubeVideoId(url);
		const lists = url.searchParams.getAll("list");
		const playlist =
			lists.length === 1 &&
			/^[\w-]{10,150}$/.test(lists[0]!) &&
			(url.pathname.replace(/\/$/, "") === "/playlist" || videoId)
				? "https://www.youtube.com/playlist?list=" + lists[0]
				: null;
		if (videoId)
			return {
				kind: "video",
				url: "https://www.youtube.com/watch?v=" + videoId,
				playlist,
			};
		if (playlist) return { kind: "playlist", url: playlist };
		return { kind: "invalid" };
	} catch {
		if (/^[a-z][a-z\d+.-]*:/i.test(text) || /^www\./i.test(text) || text.length > 200)
			return { kind: "invalid" };
		return { kind: "search", query: text };
	}
}

export function artistNames(track: Track): string {
	return track.artists.map(({ name }) => name).join(", ") || "Unknown artist";
}

export function formatDuration(seconds: number | null): string {
	if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "";
	const minutes = Math.floor(seconds / 60);
	return minutes + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");
}
